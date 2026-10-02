"""Sandboxed Pandas/Matplotlib analysis for the data_analysis tool.

The analysis code operates on a preloaded ``df`` inside a restricted
namespace:

* imports are AST-checked against a whitelist (pandas/numpy/matplotlib/math)
* dunder attribute access is rejected before anything executes
* builtins are a minimal safe set — no open/exec/import/__import__
* Matplotlib runs headless (Agg) and any open figure is persisted as a PNG
  under ``<upload_dir>/analysis`` and returned as a servable URL

Two code sources:
* LLM mode   — the configured LLM writes the pandas script from the column
               schema + question
* mock mode  — a deterministic template (describe / missing / monthly trend
               / z-score outliers) so the whole loop runs offline

Output is a plain :class:`AnalysisOutcome`; tool failures raise ToolError as
usual.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from app.agent.tools import ANALYSIS_ALLOWED_IMPORTS, ToolError, _check_analysis_imports
from app.core.config import settings
from app.models.agent import DataFile

logger = logging.getLogger(__name__)

ANALYSIS_TIMEOUT_SECONDS = 60
MAX_SUMMARY_CHARS = 4000

# Minimal builtins for generated analysis code — enough for pandas work,
# useless for escaping the sandbox. ``__import__`` is a guarded shim: the
# module must pass the same whitelist the AST check enforces, so generated
# ``import pandas`` statements work while ``import os`` is refused.
SAFE_BUILTINS: dict[str, Any] = {
    "print": print,
    "len": len,
    "range": range,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "sum": sum,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "sorted": sorted,
    "enumerate": enumerate,
    "zip": zip,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "Exception": Exception,
}


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    """__import__ shim: real semantics, whitelist-enforced."""
    import builtins

    root = (name or "").split(".")[0]
    if level != 0 or root not in ANALYSIS_ALLOWED_IMPORTS:
        raise ImportError(f"沙箱不允许导入模块: {name}")
    return builtins.__import__(name, globals, locals, fromlist, level)


SAFE_BUILTINS["__import__"] = _safe_import


@dataclass
class AnalysisOutcome:
    headline: str
    summary: str
    chart_url: Optional[str] = None


def analysis_dir() -> str:
    path = os.path.join(settings.upload_dir, "analysis")
    os.makedirs(path, exist_ok=True)
    return path


def run_analysis(data_file: DataFile, question: str, db: Any) -> AnalysisOutcome:
    """Load the file, run the analysis script, capture output and charts."""
    df = _load_dataframe(data_file)

    # Refresh the schema snapshot so the UI/agent sees up-to-date metadata.
    if data_file.row_count != len(df) or data_file.column_names != list(df.columns):
        data_file.row_count = len(df)
        data_file.column_names = [str(c) for c in df.columns]
        db.commit()

    code, source = _analysis_code(df, question)
    _check_analysis_imports(code)
    namespace = _build_namespace(df, question)

    stdout, error, chart_url = _execute(code, namespace)

    # Robustness net: small local models often emit broken pandas code. When
    # model-generated code fails, degrade to the deterministic template so the
    # user still gets a real analysis (and a note about the fallback).
    if error and source == "llm":
        logger.warning("LLM analysis code failed (%s); falling back to template", error[:120])
        tpl_columns = [str(c) for c in df.columns]
        tpl_numeric = [
            c for c in tpl_columns if pd.api.types.is_numeric_dtype(df[c])
        ]
        tpl_code = _template_code(tpl_columns, tpl_numeric, question)
        tpl_stdout, tpl_error, tpl_chart = _execute(tpl_code, _build_namespace(df, question))
        if not tpl_error:
            stdout = (
                tpl_stdout.strip()[:MAX_SUMMARY_CHARS]
                + "\n\n（模型生成的分析脚本执行失败，已自动回退到内置分析模板）"
            )
            error = None
            chart_url = tpl_chart

    parts: list[str] = []
    if stdout.strip():
        parts.append(stdout.strip()[:MAX_SUMMARY_CHARS])
    if error:
        parts.append(f"分析脚本出错：{error[:500]}")
    summary = "\n".join(parts) or "（脚本没有输出）"
    headline = _headline(question, error)

    if chart_url is None:
        chart_url = _save_open_figures()
    return AnalysisOutcome(headline=headline, summary=summary, chart_url=chart_url)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _load_dataframe(data_file: DataFile) -> pd.DataFrame:
    path = data_file.storage_path
    if not os.path.isabs(path):
        path = os.path.join(settings.upload_dir, path)
    if not os.path.exists(path):
        raise ToolError(f"数据文件已丢失：{data_file.filename}")
    try:
        if data_file.file_type == "csv":
            return _read_csv(path)
        return pd.read_excel(path)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent
        raise ToolError(f"读取数据文件失败：{exc}") from exc


def _read_csv(path: str) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return pd.read_csv(path, encoding=encoding, sep=None, engine="python")
        except UnicodeDecodeError:
            continue
    raise ToolError("无法识别 CSV 编码（尝试了 UTF-8 / GBK）")


def _build_namespace(df: pd.DataFrame, question: str) -> dict[str, Any]:
    import numpy as np
    import math

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    return {
        "__builtins__": SAFE_BUILTINS,
        "pd": pd,
        "np": np,
        "plt": plt,
        "math": math,
        "df": df,
        "question": question,
    }


def _execute(code: str, namespace: dict[str, Any]) -> tuple[str, Optional[str], Optional[str]]:
    """Run the script with a timeout; returns (stdout, error, chart_url)."""
    import io
    from contextlib import redirect_stdout

    stdout_capture = io.StringIO()
    error: Optional[str] = None
    try:
        with redirect_stdout(stdout_capture):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(exec, code, namespace)  # noqa: S102 - sandboxed
                future.result(timeout=ANALYSIS_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        error = f"分析超时（>{ANALYSIS_TIMEOUT_SECONDS}s）"
    except Exception as exc:  # noqa: BLE001 - reported to the agent
        error = f"{type(exc).__name__}: {exc}"

    plt_ns = namespace.get("plt")
    chart_url = None
    if plt_ns is not None and plt_ns.get_fignums():
        chart_url = _save_open_figures(plt_ns)
    return stdout_capture.getvalue(), error, chart_url


def _save_open_figures(plt: Any = None) -> Optional[str]:
    """Persist any open Matplotlib figures; returns the first chart URL."""
    if plt is None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    if not plt.get_fignums():
        return None
    name = f"chart_{uuid.uuid4().hex[:12]}.png"
    path = os.path.join(analysis_dir(), name)
    plt.savefig(path, dpi=110, bbox_inches="tight")
    plt.close("all")
    return f"/api/v1/agents/charts/{name}"


def _headline(question: str, error: Optional[str]) -> str:
    if error:
        return f"分析未完成（{question[:30]}）"
    return question[:40] or "数据分析完成"


def _analysis_code(df: pd.DataFrame, question: str) -> tuple[str, str]:
    """The pandas script to execute: LLM-generated in API mode, template in mock.

    Returns ``(code, source)`` where source is ``"llm"`` or ``"template"`` —
    the caller uses it to decide whether a failed run should fall back to the
    deterministic template.
    """
    if settings.LLM_PROVIDER == "openai":
        code = _llm_analysis_code(df, question)
        if code:
            return code, "llm"
        logger.warning("LLM analysis code generation failed; using template")

    columns = [str(c) for c in df.columns]
    numeric = [
        c for c in columns
        if pd.api.types.is_numeric_dtype(df[c])
    ]
    return _template_code(columns, numeric, question), "template"


def _llm_analysis_code(df: pd.DataFrame, question: str) -> Optional[str]:
    from app.rag.llm import LLMError, get_llm_provider

    schema = "\n".join(
        f"- {name}: {dtype}" for name, dtype in df.dtypes.items()
    )
    prompt = (
        "你是数据分析工程师。写一段 Python 脚本（只输出代码，不要解释），"
        "用已加载的 pandas DataFrame 变量 df 完成以下分析：\n"
        f"{question}\n\n数据列：\n{schema}\n\n"
        "要求：\n"
        "1. 只能 import pandas/numpy/matplotlib/math。\n"
        "2. 用 print 输出关键统计结论（中文）。\n"
        "3. 如涉及趋势，请按月份汇总并用 plt 画图（不要调用 plt.show）。\n"
        "4. 不要访问文件系统或网络。"
    )
    try:
        raw = get_llm_provider().complete(
            [{"role": "user", "content": prompt}]
        )
    except LLMError as exc:
        logger.warning("LLM analysis codegen failed: %s", exc)
        return None
    return _strip_code_fence(raw)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [line for line in lines if not line.strip().startswith("```")]
        return "\n".join(lines).strip()
    return text


def _template_code(columns: list[str], numeric: list[str], question: str) -> str:
    """Deterministic offline analysis: overview + monthly trend + outliers."""
    return f'''
print("数据概览：{{}} 行 × {{}} 列".format(len(df), len(df.columns)))
print("列：" + ", ".join({columns!r}))
print("\\n各列缺失值：")
print(df.isna().sum().to_string())

numeric_cols = {numeric!r}
if numeric_cols:
    print("\\n数值列统计：")
    print(df[numeric_cols].describe().to_string())

# 月度趋势：尝试解析日期列并按月汇总第一个数值列
date_col = None
for col in df.columns:
    parsed = pd.to_datetime(df[col], errors="coerce")
    if parsed.notna().mean() > 0.8:
        date_col = (col, parsed)
        break

if date_col and numeric_cols:
    col_name, dates = date_col
    work = df.copy()
    work["__month__"] = dates.dt.to_period("M").astype(str)
    target = numeric_cols[0]
    monthly = work.groupby("__month__")[target].sum()
    print("\\n按月汇总（{{}}）：".format(target))
    print(monthly.to_string())

    ax = monthly.plot(kind="line", marker="o", figsize=(8, 4), title=target)
    ax.set_xlabel("月份")
    ax.set_ylabel(target)
    plt.tight_layout()

    if len(monthly) >= 3:
        mean, std = monthly.mean(), monthly.std()
        if std and std > 0:
            z = (monthly - mean) / std
            outliers = z[abs(z) > 1.5]
            if len(outliers):
                print("\\n异常月份（|z|>1.5）：")
                print(outliers.to_string())
            else:
                print("\\n未发现明显异常月份（|z|>1.5）。")
else:
    print("\\n未检测到可解析的日期列或数值列，跳过趋势分析。")
'''


def outcome_to_json(outcome: AnalysisOutcome) -> str:
    return json.dumps(outcome.__dict__, ensure_ascii=False)
