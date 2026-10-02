# AI 智能知识库与 Agent 工作流平台

> AI Knowledge Base & Agent Workflow Platform — 一个前后端分离的全栈 AI 应用，
> 覆盖 RAG 知识库、SSE 流式对话、Agent 工具调用、数据分析沙箱与 MCP 协议实现。

## 项目简介

从零构建的企业级 AI 平台：上传文档自动解析并向量化入库，基于 **pgvector** 做向量检索与 **Reranker** 重排，
回答问题带**文档级来源引用**；对话支持 **SSE 流式输出**、**多模态图片输入**（本地 Qwen3.5 视觉模型）与 Markdown 渲染；
**Agent 状态机**驱动工具调用（知识检索 / 计算器 / Pandas 数据分析出图），工具经 **自建 MCP Server**（JSON-RPC 2.0）发现与执行；
三层 Provider（Embedding / Reranker / LLM）全部抽象可切换——默认零依赖离线可跑，一行 `.env` 切到真实厂商或本地模型服务。

**亮点**：
- 🧩 **MCP 手写实现**：不依赖第三方 SDK，按协议实现 JSON-RPC 2.0 / streamable-HTTP（握手、会话、tools/list、tools/call），含 SSRF 防护与注入拦截
- 🖼️ **本地多模态模型**：model-service 容器加载 Qwen3.5-0.8B 权重（transformers），看图说话 / 工具调用全程真实推理
- 🔒 **数据分析沙箱**：LLM 生成的 Pandas 代码经 AST 导入白名单 + 受限 builtins + 超时控制执行，Matplotlib 图表内嵌对话
- ⚡ **真实异步链路**：Celery + Redis 解析/嵌入管线，pgvector HNSW 索引，6 容器 Docker Compose 一键启动
- ✅ **测试完备**：127 个 pytest 用例 + 6 套端到端验收脚本（`docs/e2e_phase2-7.py`），每条链路都有实测兜底

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3 · TypeScript · Vite · Element Plus · Pinia · ECharts · marked + highlight.js |
| 后端 | Python 3.14 · FastAPI · SQLAlchemy 2 · Alembic · JWT |
| 数据 | PostgreSQL 16 + pgvector（HNSW） · Redis 7 |
| 异步 | Celery + Redis（解析/嵌入管线） |
| AI | OpenAI 兼容 Provider 抽象 · 自建 MCP Server（JSON-RPC 2.0） · 本地 Qwen3.5（transformers） |
| 部署 | Docker Compose（backend / worker / frontend / model-service / postgres / redis） |

## 架构

```
Vue 3 + TypeScript
│  HTTP / SSE（流式对话）
▼
FastAPI ── MCP Client ──► MCP Server（JSON-RPC 2.0, /api/mcp）
│  ├ 知识库 / RAG        │  calculator · weather · http_request
│  ├ Agent（工具循环）    │  knowledge_search · python_analysis
│  └ 用户认证 / 任务
├──────────────┬──────────────────┐
▼              ▼                  ▼
PostgreSQL     Redis             model-service
+ pgvector     Celery broker     OpenAI 兼容
HNSW 索引      缓存 / 队列        Qwen3.5-0.8B / 桩实现
```

## 快速启动

```powershell
cp .env.example .env          # 按需修改；默认离线可跑（mock Provider）
docker compose up -d --build  # 六容器一键启动
# 前端 http://localhost:5173 · Swagger http://localhost:8000/api/docs
```

接真实模型：`.env` 里配 `EMBEDDING_PROVIDER` / `LLM_PROVIDER`（任意 OpenAI 兼容厂商），
或启用本地 Qwen3.5（见下方「本机真实模型」）。详见下文完整文档。

---

## 阶段进度（对齐《项目开发框架》第十四节）

| 阶段 | 主要内容 | 状态 |
| --- | --- | --- |
| Phase 1 | Vue3 + FastAPI + PostgreSQL + Redis + Docker + JWT + Dashboard | ✅ 完成 |
| Phase 2 | 知识库 CRUD、文件上传、PDF/Word/TXT 解析、异步任务、进度显示 | ✅ 完成 |
| Phase 3 | Embedding、pgvector、向量搜索、Reranker、RAG、来源引用 | ✅ 完成 |
| Phase 4 | LLM Provider、SSE、对话历史、Markdown、代码高亮 | ✅ 完成 |
| Phase 5 | Agent、Tool Calling、Agent State、Python 数据分析 | ✅ 完成 |
| Phase 6 | MCP Client、MCP Server、Tool Discovery、Tool Execution | ✅ 完成 |
| Phase 7 | Docker、日志、异常处理、权限、测试、API 文档、模型服务 | ✅ 完成 |

---

## Phase 6 交付内容（MCP）

| 模块 | 说明 |
| --- | --- |
| 自建 MCP Server | `app/mcp/server.py`：按 MCP 规范实现 JSON-RPC 2.0 / streamable-HTTP 子集，挂载在 `POST /api/mcp`（initialize 握手 + `Mcp-Session-Id` 会话 + tools/list + tools/call）。零第三方 SDK 依赖，协议形状与标准客户端兼容 |
| 五个 MCP 工具 | calculator / knowledge_search（复用检索管线）/ weather（Open-Meteo 真实数据 + 离线降级）/ http_request（SSRF 防护：仅公网 http/https）/ python_analysis（复用 Phase 5 沙箱） |
| MCP Client | `app/mcp/client.py`：initialize → tools/list → tools/call，支持任意兼容端点（`MCP_SERVER_URL` 可指向第三方服务器） |
| Tool Discovery | `app/mcp/discovery.py`：把远端工具描述包装为 `mcp_*` 代理工具注册进 Agent 工具注册表，并同步 `tools` 表（`type='mcp'`）；服务端新增工具零 Agent 侧代码。启动时自动发现（延迟 1s），`POST /mcp/refresh` 可重发现 |
| 管理页 | 「MCP 工具」页：连接状态、工具列表、测试调用面板（选工具 + JSON 参数 → 实时结果） |

## Phase 7 交付内容（加固与模型服务）

| 模块 | 说明 |
| --- | --- |
| 请求日志中间件 | 每个请求记录 `方法 路径 -> 状态码 (耗时)`，健康检查端点降噪 |
| 全局异常处理 | 兜底 `Exception` 处理器：完整堆栈进日志，客户端统一拿到 JSON 500，不泄漏内部细节 |
| 模型服务 | `model-service/`：自研 OpenAI 兼容桩（/v1/embeddings 哈希向量、/v1/rerank 词汇重排、/v1/chat/completions 摘录式回答 + SSE 流式），独立容器。`EMBEDDING/RERANKER/LLM_*` 指向 `http://model-service:9000/v1` 即可让整条 Provider 链路跑真实 HTTP 而不依赖外部 API Key；换真实厂商只改 .env |
| 可观测性 | 六容器编排（backend / worker / frontend / postgres+pgvector / redis / model-service），全部带健康检查 |

## Phase 3-5 交付内容（已完成，保持可用）

## Phase 5 交付内容（已完成）
| 模块 | 说明 |
| --- | --- |
| Agent 执行器 | `app/agent/executor.py`：状态机循环（思考→工具调用→工具结果→再判断→最终答案），上限 8 轮；每一步以 SSE `step` 事件实时推送前端时间线 |
| 工具注册表 | `app/agent/tools.py`：`knowledge_search`（复用 Phase 3 检索管线并返回来源）/ `calculator`（AST 安全求值，无 eval）/ `data_analysis`；启动时同步进 `tools` 表（enabled 开关） |
| Function Calling | LLM Provider 新增 `complete_with_tools`：OpenAI 兼容 `/tools` API（真实模型）；mock 用规则意图识别（算术检测/数据分析关键词/文件名匹配），离线可演示完整循环 |
| 数据分析沙箱 | `app/agent/sandbox.py`：Pandas 统计 + Matplotlib 出图；导入白名单（AST 校验）、双下划线访问拒绝、受限 builtins、60s 超时；mock 模式用确定性模板脚本（概览/缺失值/按月汇总/|z|>1.5 异常检测），LLM 模式由模型生成脚本 |
| 数据文件 | `POST /agents/data-files` 上传 Excel/CSV（≤25MB），落盘并记录行数/列名快照；图表 PNG 存 `<upload_dir>/analysis` 经 `/agents/charts/{name}` 提供服务（随机 12-hex 文件名 + basename 校验防穿越） |
| Agent 管理 | `agents` 表 + CRUD（名称/系统提示词/模型覆盖/工具白名单），前端「Agent」页管理；对话创建可绑定 agent |
| 前端 | 「Agent」页（CRUD + 工具一览 + 数据文件上传）；对话页新增 Agent 选择器、数据文件上传、步骤时间线（思考/调用/结果）、Markdown 图表内嵌展示 |
| 镜像 | 基础镜像升到 `python:3.14-slim`（与本地验证环境一致；numpy 2.5.x 需 ≥3.12），新增 pandas/numpy/matplotlib/openpyxl |

### Phase 4 交付内容

| 模块 | 说明 |
| --- | --- |
| SSE 流式对话 | `POST /conversations/{id}/chat` 流式返回 `meta → [sources] → delta* → done`（或 `error`）；nginx 侧禁用缓冲；用户消息先落库，断流不丢输入 |
| 对话历史 | `conversations` + `messages` 表（迁移 0004，消息含 sources/tool_calls/model），会话列表/详情/重命名/删除、跨用户 404 隔离；自动用首条消息命名，重命名可改 |
| 历史上下文 | 普通对话携带最近 10 条消息进入 Prompt；RAG 对话按当前检索独立作答（带引用） |
| LLM Provider 流式 | `stream_complete` / `stream_rag_answer`：OpenAI 兼容 SSE 逐字解析（`_parse_stream_line` 容错 keep-alive/role-only 块）；mock 分片输出，输出明确标注 mock |
| RAG 对话 | 会话可绑定知识库：回答前先检索并推送 `sources` 事件，答案与消息都带引用（文件名/页码/相似度） |
| Dashboard | `conversation_count` / `ai_call_count` / 近 7 日 AI 调用趋势接真实数据 |
| 前端 Chat 页 | 左侧会话列表 + 右侧对话区；SSE 增量渲染（fetch 流式解析）；Markdown（marked）+ DOMPurify 消毒 + highlight.js 代码高亮；来源标签、停止生成（AbortController） |

### Phase 3 交付内容（已完成，保持可用）

| 模块 | 说明 |
| --- | --- |
| Embedding Provider 抽象层 | `app/rag/embeddings.py`：`mock`（确定性离线哈希向量，零依赖默认）/ `openai`（任意 OpenAI 兼容 `/v1/embeddings`：OpenAI、SiliconFlow、智谱等）/ `local`（sentence-transformers 懒加载，装了才启用）。批量嵌入、维度校验、L2 归一化 |
| Reranker Provider 层 | `app/rag/reranker.py`：`lexical`（内置字符 n-gram 重排，离线默认）/ `api`（Jina/Cohere 风格 `/rerank`）/ `none`（保留向量序） |
| LLM Provider 抽象层 | `app/rag/llm.py`：`mock`（摘录式离线作答，输出明确标注 mock）/ `openai`（任意 `/v1/chat/completions`）。共享 RAG Prompt（强制引用【片段 i】），Phase 4 在此扩展 SSE 流式 |
| pgvector 向量列 | `document_chunks.embedding`：PostgreSQL 用 `vector(EMBEDDING_DIM)` + HNSW 余弦索引；SQLite 预览用 JSON + Python 暴力余弦，ORM 层同一 `list[float]` 类型（`app/models/types.py`）。None 强制写 SQL NULL，杜绝 JSON `'null'` 混过 `IS NOT NULL` 过滤 |
| 摄取管线嵌入 | 解析任务在切片后执行 Embedding 阶段（进度 80→95 按批次推进），`documents.embedding_model` 记录向量来源（如 `mock:1024` / `openai:bge-m3`），失败给出明确错误 |
| 重建向量 | `POST .../documents/{id}/reembed` 创建 `document.embed` 任务：换 Embedding 模型后或嵌入失败后，对已有切片重新生成向量 |
| 检索管线 | `app/rag/retrieval.py`：Query Embedding → Top-K 向量检索（pgvector `<=>` / SQLite 回退）→ Reranker 重排 → 截取最终 Top-K，结果带文件名/页码/切片号/相似度 |
| RAG 问答 | `POST .../{kb_id}/query`：检索 → 构造带编号片段的 Prompt → LLM → 答案 + 来源引用列表（文件名、页码、相似度、摘要）。同步实现，Phase 4 换 SSE |
| 向量检索 API | `POST .../{kb_id}/search`：只检索不调用 LLM，用于检索调参与前端展示 |
| 前端 | 知识库页新增「AI 问答（RAG）」面板：问答/仅检索双模式、答案与来源引用（编号 + 文件名 + 页码 + 相似度）、文档表新增「已向量化」标识与「重建向量」按钮 |
| 数据库迁移 | Alembic `0003_chunk_embeddings_and_rag`：`document_chunks.embedding` + `documents.embedding_model`；PostgreSQL 建 `vector` 扩展与 HNSW 索引，SQLite 走 JSON 分支 |
| AI 提供商错误映射 | Embedding/Reranker/LLM 失败统一映射 502 并带明确 detail，与「检索为空（200）」区分 |
| Docker 全栈实机验证 | `pgvector/pgvector:pg16` 镜像上迁移 0003 实测通过：`vector` 扩展 0.8.6、`embedding vector(1024)` 列、HNSW `vector_cosine_ops` 索引全部就位；真实 Celery worker 消费队列完成 解析→嵌入→检索→问答→重建向量 全链路（e2e Phase 2/3 均 ALL CHECKS PASSED） |

**首次真实 Docker 构建时修复的三个隐患**（本地环境有、容器里没有的偏差）：

1. `backend/Dockerfile` 的 `COPY tests` 与 `.dockerignore` 的 `tests/` 互相矛盾 → 删除 COPY（镜像不跑测试）。
2. `pydantic-settings` 旧 pin（2.6.1）缺 `NoDecode`（2.7.0+ 才有），容器启动即 ImportError → requirements 全面对齐到本地已验证版本。
3. `EmailStr` 依赖的 `email-validator` 未声明，容器内报 ModuleNotFoundError → 补 pin。

另：pgvector 距离表达式需显式 `op("<=>", return_type=Float)`，否则距离列继承向量类型、结果处理阶段把浮点当向量文本解析报错（该 bug 只在真实 PostgreSQL 上出现，SQLite 回退路径不触发）。

### Phase 1/2 交付内容（已完成，保持可用）

**Phase 2**：知识库 CRUD（按 `user_id` 归属校验，越权返回 404）；文档上传（multipart，限 pdf/docx/txt、25 MB，落盘 `<UPLOAD_DIR>/<user_id>/<uuid>.<ext>`）；解析管线（pypdf / python-docx / TXT 编码探测 → 清洗 → 切片，窗口 800/重叠 120，保留页码与偏移）；Celery + Redis 异步任务（进度 5→30→60→80→100，重试上限 2，毒文档不死循环）；Redis 不可用自动内联解析（30s 缓存探活）；前端进度条 + 任务页轮询；删除级联（DB 行 + 磁盘文件，PG/SQLite 行为一致）；Alembic `0002` 迁移。

**Phase 1**：

| 模块 | 状态 |
| --- | --- |
| Vue 3 + TypeScript + Vite + Element Plus + Pinia + Vue Router + Axios + ECharts | ✅ |
| FastAPI + Pydantic + SQLAlchemy 2 + Alembic + JWT | ✅ |
| PostgreSQL 16（`users` 迁移）+ Redis 7（健康检查 / 缓存 / 队列） | ✅ |
| 用户注册 / 登录 / `/me`(JWT Bearer) | ✅ |
| Dashboard：统计卡片 + 近 7 日趋势 + 任务状态分布 + 最近任务 | ✅ |
| Docker Compose 一键启动（`postgres` `redis` `backend` `worker` `frontend`） | ✅ |
| `/api/docs` Swagger UI · `/api/redoc` · `/api/openapi.json` | ✅ |

---

## 目录结构

```
ai-platform/
├── backend/                     # FastAPI 后端
│   ├── app/
│   │   ├── api/v1/              # auth、dashboard、knowledge_bases、documents、tasks、rag、health
│   │   ├── core/                # 配置、数据库、Redis、安全、日志
│   │   ├── models/              # user、knowledge（KB/Document/Chunk/Task）、types（向量列类型）
│   │   ├── schemas/             # Pydantic Schema（含 rag.py）
│   │   ├── services/            # 业务逻辑（auth / KB / document / task / dashboard / storage / rag）
│   │   ├── repositories/        # 数据访问（只做 SQL，不 commit；含向量检索）
│   │   ├── rag/                 # parsers / cleaner / chunker / embeddings / reranker / llm / retrieval
│   │   ├── workers/             # Celery app + 解析与嵌入任务（parse→clean→chunk→embed→persist）
│   │   └── main.py
│   ├── alembic/versions/        # 0001 users、0002 知识库与任务、0003 向量与 pgvector
│   ├── tests/                   # pytest：安全、模型元数据、RAG 解析/切片、Provider、检索、嵌入管线
│   ├── uploads/                 # 上传文件（运行期生成，已 gitignore）
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                    # Vue 3 + Vite
│   ├── src/
│   │   ├── api/                 # axios 封装 + auth / dashboard / knowledge(tasks)
│   │   ├── stores/ router/ views/ components/ types/ utils/ styles/
│   │   ├── views/               # Login / Register / AppLayout / Dashboard / Knowledge / Tasks / NotFound
│   │   └── main.ts
│   ├── Dockerfile               # 多阶段：node build → nginx serve
│   └── nginx.conf               # SPA fallback + /api 反代
├── docs/                        # 验收脚本与开发期排查工具（见下方「验收与测试」）
├── docker-compose.yml           # postgres + redis + backend + worker + frontend
├── .env.example
└── README.md
```

---

## 快速启动

### 方式 A：Docker Compose 一键启动（推荐 · 完整异步链路）

> 前置条件：Docker Desktop / Docker Engine 24+，本机 5173 / 8000 / 5432 / 6379 端口空闲。

```powershell
# 1. 复制环境变量并按需修改（至少替换 SECRET_KEY、POSTGRES_PASSWORD）
Copy-Item .env.example .env

# 2. 构建并启动（首次约 3-5 分钟）
docker compose up -d --build

# 3. 查看日志
docker compose logs -f backend
docker compose logs -f worker
```

| 入口 | URL |
| --- | --- |
| 前端 | http://localhost:5173 |
| Swagger UI | http://localhost:8000/api/docs |
| 健康检查 | http://localhost:8000/api/v1/health |

`backend` 容器启动时会自动执行 `alembic upgrade head`；上传的文档由 `worker` 容器异步解析并生成向量（`uploads_data` 卷在 backend 与 worker 之间共享）。

> 国内网络提示：Docker Hub 直连常被阻断，`docker compose up` 拉镜像会超时。可在 Docker Desktop 设置（或 `%USERPROFILE%\.docker\daemon.json`）配置 `registry-mirrors`（如 `https://docker.m.daocloud.io`、`https://docker.1ms.run`、`https://docker.1panel.live`），重启 Docker 后再拉取。

### 方式 B：本地零依赖预览（SQLite + 内联解析，不需要 PostgreSQL / Redis / Docker）

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

$env:DATABASE_URL = "sqlite:///./ai_platform.db"   # 或用 backend/.env 指定
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

没有 Redis 时，上传接口会直接在内联模式下完成解析，响应里 `dispatch` 字段为 `inline`，文档上传后即 `completed`（前端会提示「本地模式：已同步完成解析」）。
如需强制内联（即使 Redis 在线也走内联），设置 `CELERY_TASK_ALWAYS_EAGER=true`。

### 方式 C：本地完整异步（PostgreSQL + Redis + Celery worker）

```powershell
# 1. 起依赖（或使用本机已装的 PostgreSQL 16 / Redis 7）
docker compose up -d postgres redis

# 2. 后端
cd backend
Copy-Item .env.example .env      # 按需修改连接信息
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 3. Worker（Windows 必须加 --pool=solo）
celery -A app.workers.celery_app:celery_app worker --loglevel=INFO --pool=solo
```

### 前端（本地开发）

```powershell
cd frontend
npm install
Copy-Item .env.example .env.local
npm run dev            # http://localhost:5173，/api/* 反代到 127.0.0.1:8000
npm run build          # vue-tsc 类型检查 + vite 生产构建
```

---

## 文档解析管线与异步策略

```
上传 → 校验类型/大小 → 落盘 → 写 documents(pending) + tasks(pending)
     → 有 broker：Celery 排队（worker 执行）
     → 无 broker：API 内联执行
执行： parse_document → clean_pages → chunk_document → 写 document_chunks
     → Embedding（按批次，进度 80→95）→ 更新 embedding_model → 完成(100)
     每个阶段提交一次进度（5/30/60/80/95/100），失败则记录 error_message 并把文档置为 failed
```

- 解析任务的重试上限为 2 次；重试耗尽后返回已持久化的失败结果，不会因为“毒文档”无限重试。
- Embedding 失败不会丢失已产出的切片：`POST .../documents/{id}/reembed` 可对已有切片重新生成向量（换模型后同样适用）。
- 上传接口读到的结果是解析后的最终状态：内联解析在另一个 Session 里提交，请求 Session 会先 `expire_all()`，避免身份映射返回解析前的脏数据。
- Repository 层不提交事务，事务边界由 Service 负责（删除类操作在 Service 里统一 `commit`）。
- `CELERY_BROKER_URL` 默认由 `REDIS_*` 推导（broker 用 db1、result backend 用 db2）。Redis 专用的传输超时（`socket_connect_timeout` / `retry_policy` 等）只在 URL 确实是 `redis://` 时注入，换成别的传输不会把无关参数塞给驱动。
- 注意：上传接口的 broker 探活只认 Redis。若刻意把 broker 换成非 Redis 传输，探活会返回 False，上传将走内联解析、worker 空闲（本项目部署形态固定为 Redis，故保持这一行为）。

---

## RAG 管线（Phase 3）

```
摄取： 切片 → Embedding Provider（批量）→ unit vector → document_chunks.embedding
检索： 问题 → Query Embedding → Top-K 向量检索（pgvector <=> / SQLite 暴力余弦）
     → Reranker 重排 → 最终 Top-K（带文件名/页码/切片号/相似度）
问答： 检索结果 → 编号片段 Prompt（强制引用【片段 i】）→ LLM → 答案 + 来源列表
```

- 三层 Provider 全部经 `.env` 切换，默认 `mock/lexical/mock`，零 API Key 可跑通全链路（mock LLM 的输出明确标注为离线摘录）。
- 接入真实模型：`EMBEDDING_PROVIDER=openai` + `EMBEDDING_API_BASE/KEY/MODEL`（如 SiliconFlow `BAAI/bge-m3`，dim 1024），LLM 同理；`RERANKER_PROVIDER=api` 接 Jina/Cohere 风格 `/rerank`。
- `EMBEDDING_DIM` 在迁移 0003 里固化为 pgvector 列宽，修改它必须配新迁移；Provider 返回维度不符会在写入前报错。
- 检索 API 与 RAG 问答均为同步实现（框架要求 Phase 4 才引入 SSE 流式）。

---

## 本机真实模型（Qwen3.5-0.8B）

三套 Provider 默认都是离线实现（`mock` LLM / 确定性向量），零依赖可演示。要接**真实权重**，无需改代码——`model-service` 的 chat 端点可以跑本地模型（`MODEL_SERVICE_LLM_BACKEND=qwen`），平台侧只需把 `LLM_PROVIDER` 指过去。

### 模型与下载

| 项 | 值 |
| --- | --- |
| 仓库 | [`Qwen/Qwen3.5-0.8B`](https://www.modelscope.cn/models/Qwen/Qwen3.5-0.8B)（ModelScope 镜像，国内可直连） |
| 架构 | `Qwen3_5ForConditionalGeneration`：24 层混合线性注意力（每 4 层 1 层 full attention）+ 视觉塔，词表 248K，文本侧 256K 上下文 |
| 权重 | `model.safetensors-00001-of-00001.safetensors` 1.67 GB，sha256 `04b1c301…f1fe4696`（下载后已校验一致） |
| 许可 | Apache-2.0 |
| 依赖 | `transformers>=5.17`（原生带 `qwen3_5` 模块）+ torch |

```powershell
# 1. 依赖（在 model-service 目录建独立 venv，避免污染后端环境）
cd model-service
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-qwen.txt

# 2. 权重（ModelScope，实测 ~9 MB/s；也可用 hf-mirror 同源文件）
#    下载后核对 sha256 与上表一致
#    F:\ai-models\Qwen3.5-0.8B\  ← QWEN_MODEL_PATH 指向这里

# 3. 起模型服务（CPU；有 CUDA 版 torch 时会自动用 GPU）
$env:MODEL_SERVICE_LLM_BACKEND="qwen"
$env:QWEN_MODEL_PATH="F:\ai-models\Qwen3.5-0.8B"
$env:QWEN_THREADS="16"
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 9000
```

平台侧（后端 `.env` 或环境变量）用上面 `.env.example` 里的预设 (b)：`LLM_PROVIDER=openai` + `LLM_API_BASE=http://127.0.0.1:9000/v1` + `LLM_MODEL=qwen3.5-0.8b` + `LLM_TEMPERATURE=0.7` + `LLM_MAX_OUTPUT_TOKENS=256` + `LLM_TIMEOUT_SECONDS=300`。
平台跑在 Docker 里、模型服务在宿主机时，把 base 换成 `http://host.docker.internal:9000/v1`。

### 同一套东西跑在 Docker 里（本机未验证，见下方边界说明）

`docker-compose.yml` 已经把 `MODEL_SERVICE_LLM_BACKEND` / `QWEN_*` 透传给 `model-service`，并把权重目录挂到容器内 `/models/qwen`：

```bash
# .env 里加三行
WITH_QWEN=true
MODEL_SERVICE_LLM_BACKEND=qwen
QWEN_MODEL_DIR=F:/ai-models/Qwen3.5-0.8B
docker compose up -d --build model-service
```

容器 CPU 推理同样能跑；GPU 需要 Docker 的 GPU 直通 + CUDA 版 torch。

### 已经打通的能力

- **同步 + SSE 流式**：`/v1/chat/completions` 两种模式都由真实模型驱动，平台对话页逐字出字（实测首字延迟约 1–2s，随后按 CPU 速度推进）。
- **RAG 引用**：检索片段 → 真实模型作答 → 答案带 `来源：【片段 i】`，与来源事件、消息引用一起落库。
- **工具调用（Agent 循环）**：模型按 Qwen 模板输出 `<tool_call><function=…>` XML，`qwen_backend` 翻译成 OpenAI `tool_calls`，Phase 5 的 Agent 循环因此可以驱动真实模型。实测一条完整链路：

  ```
  thought → tool_call(calculator, {"expression": "(1200+980)*1.12"})
          → tool_result(2441.6)
          → 最终答案：「计算结果为 2441.6。计算过程如下：…2180 × 1.12 = 2441.6」
  ```

  注意：Qwen 模板要求 `tool_calls[].function.arguments` 是 **mapping**，而 OpenAI 线上格式是 JSON 字符串——`main.py` 的 `_normalise_tool_call` 做这层转换；少了它，Agent 第二轮回放自己的工具调用时会直接 `TypeError: Can only get item pairs from a mapping`（这个坑已在契约测试里锁住）。
- **思考模式可选**：`QWEN_ENABLE_THINKING=true` 打开推理段（回答里不泄漏，只作为 `reasoning_content` 返回）。

### 实测数据与调参结论（本机 RTX 3060 Laptop 6GB / 16 线程 CPU）

| 配置 | 结果 |
| --- | --- |
| CPU `float32` | **约 4.5 tok/s**（40 token ≈ 9s；一段 100 字答案约 20s） |
| CPU `bfloat16` | 0.54 tok/s（慢 8 倍，所以 CPU 默认 `float32`） |
| RAG 提示词 · temp 0.2 · **无 top_k** | 0/3 可用 |
| RAG 提示词 · temp 0.7 · **无 top_k** | 0/3 可用 |
| RAG 提示词 · temp 0.7 · **top_k=20** | **3/3 可用**（当前默认） |
| 思考模式 + top_k=20 | 3/3 可用，但约 58s/答、输出近千字（CPU 下不推荐） |

结论：**`top_k=20` 是这个小模型不跑偏的关键**（Qwen 官方对非思考模式的推荐值），温度太低反而容易抄提示词里的示例；`qwen_backend` 因此内置了「退化答案检测 + 逐次升温重试」（空答案或只有一行 `来源：` 时用 0.7 / 1.0 重采样，退化答案只有 10 来个 token，重试几乎不花时间）。

### 已知边界（如实说明）

- CPU 推理就是这个速度，交互式演示建议 `LLM_MAX_OUTPUT_TOKENS=256`；想要流畅体验需要 CUDA 版 torch + `QWEN_DEVICE=cuda`。本机 GPU 可用（驱动 591.74 / CC 8.6），但国内可及的 CUDA wheel 源实测只有 0.23–0.30 MB/s（2.5GB 要两小时以上），所以本次交付跑的是 CPU 路径。
- 混合线性注意力的优化算子（`causal_conv1d` / `flash-linear-attention`）在 Windows CPU 上没有可用轮子，模型走的是 PyTorch 参考实现（transformers 会打印提示）。
- **Docker 形态下的真实模型未在本机验证**（本机 Docker 守护进程未运行）：镜像已按 `--build-arg WITH_QWEN=true` 支持装推理栈并 `COPY qwen_backend.py`，但容器内 GPU 直通、挂载权重的实机跑通留给有 Docker 的环境。
- Embedding / Reranker 仍是确定性实现（`model-service` 的哈希向量与词法重排）；换真实向量模型是同样机械的一步，不影响本次 LLM 接入。

### 验证

```powershell
# 模型服务（契约/流式/工具调用）+ 平台（SSE 对话 / RAG 引用 / Agent 工具循环）
python docs/verify_local_llm.py
python docs/verify_local_llm.py --skip-platform     # 只验模型服务
python docs/_sampling_sweep.py                      # 采样配置对比（top_k / 温度 / 思考模式）
```

---

## 关键 API

| Method | Path | 说明 | 鉴权 |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/register` | 注册 | 否 |
| `POST` | `/api/v1/auth/login` | 登录获取 JWT | 否 |
| `GET` | `/api/v1/auth/me` | 当前用户 | Bearer |
| `POST` | `/api/v1/auth/logout` | 登出（前端清 token） | Bearer |
| `GET` | `/api/v1/dashboard` | 统计 + 近 7 日趋势 + 任务状态分布 + 最近任务 | Bearer |
| `GET` | `/api/v1/knowledge-bases` | 知识库列表（含 `document_count`） | Bearer |
| `POST` | `/api/v1/knowledge-bases` | 新建知识库 | Bearer |
| `GET` | `/api/v1/knowledge-bases/{id}` | 知识库详情 | Bearer |
| `PATCH` | `/api/v1/knowledge-bases/{id}` | 局部更新（name / description） | Bearer |
| `DELETE` | `/api/v1/knowledge-bases/{id}` | 删除知识库（级联文档/切片/任务 + 清理文件） | Bearer |
| `GET` | `/api/v1/knowledge-bases/{id}/documents` | 文档列表 | Bearer |
| `POST` | `/api/v1/knowledge-bases/{id}/documents` | 上传文档并排队解析 | Bearer |
| `DELETE` | `/api/v1/knowledge-bases/{id}/documents/{doc_id}` | 删除文档（含磁盘文件） | Bearer |
| `POST` | `/api/v1/knowledge-bases/{id}/documents/{doc_id}/reembed` | 重建文档向量（`document.embed` 任务） | Bearer |
| `POST` | `/api/v1/knowledge-bases/{id}/search` | 向量检索（Top-K + 文件名/页码/相似度，不调 LLM） | Bearer |
| `POST` | `/api/v1/knowledge-bases/{id}/query` | RAG 问答（答案 + 来源引用） | Bearer |
| `GET/POST` | `/api/v1/conversations` | 会话列表 / 创建（可绑定知识库与 Agent） | Bearer |
| `GET/PATCH/DELETE` | `/api/v1/conversations/{id}` | 会话详情（含全部消息）/ 重命名 / 删除 | Bearer |
| `POST` | `/api/v1/conversations/{id}/chat` | 发送消息，SSE 流式返回（meta/sources/step/delta/done） | Bearer |
| `GET/POST/PATCH/DELETE` | `/api/v1/agents` | Agent CRUD（名称/提示词/模型覆盖/工具白名单） | Bearer |
| `GET` | `/api/v1/agents/tools` | 工具注册表（含启用状态） | Bearer |
| `GET/POST/DELETE` | `/api/v1/agents/data-files` | 数据文件列表 / 上传 Excel-CSV / 删除 | Bearer |
| `GET` | `/api/v1/agents/charts/{name}` | 分析图表 PNG（随机文件名，无鉴权） | 否 |
| `GET/POST` | `/api/v1/mcp/tools` / `/mcp/refresh` | MCP 发现状态 / 重新发现 | Bearer |
| `POST` | `/api/v1/mcp/call` | 直接调用 MCP 工具（测试面板） | Bearer |
| `POST` | `/api/mcp` | 自建 MCP Server（JSON-RPC：initialize/tools/list/tools/call） | 否 |
| `GET` | `/api/v1/tasks?status=completed` | 任务列表（可按状态筛选） | Bearer |
| `GET` | `/api/v1/tasks/{id}` | 单个任务（进度轮询） | Bearer |
| `GET` | `/api/v1/health` | 健康检查（DB + Redis） | 否 |

上传响应示例：

```json
{
  "document": {
    "id": 12, "knowledge_base_id": 3, "filename": "销售数据说明.txt",
    "file_type": "txt", "file_size": 2766, "status": "completed", "progress": 100,
    "chunk_count": 2, "character_count": 946, "page_count": 1
  },
  "task": { "id": 9, "type": "document.parse", "status": "completed", "progress": 100,
            "result": { "chunk_count": 2, "character_count": 946, "page_count": 1 } },
  "dispatch": "inline"
}
```

---

## 数据库表

| 表 | 用途 |
| --- | --- |
| `users` | 账号、邮箱、密码哈希 |
| `knowledge_bases` | 用户知识库（name / description / 时间戳） |
| `documents` | 文件名、类型、大小、存储路径、状态、进度、页数 / 切片数 / 字符数、错误信息 |
| `document_chunks` | 切片内容、`chunk_index`、页码、字符偏移、token 估算、`embedding` 向量（PG=pgvector `vector(1024)` + HNSW 索引 / SQLite=JSON） |
| `documents` | …、`embedding_model`（向量来源，如 `mock:1024`） |
| `tasks` | 任务类型（`document.parse` / `document.embed`）、状态、进度、`celery_task_id`、payload、result、错误信息、起止时间 |
| `conversations` | 用户对话：标题、绑定的知识库（SET NULL）与 Agent、时间戳 |
| `messages` | 角色、内容、来源引用 JSON、`tool_calls`（Agent 步骤日志）、产生它的模型 |
| `agents` | 名称、系统提示词、模型覆盖、启用的工具列表 |
| `tools` | 工具注册表：名称、描述、类型（builtin / 预留 mcp）、config、启用状态 |
| `data_files` | 分析用 Excel/CSV：文件名、路径、行数、列名快照 |

结构变更一律通过 Alembic 迁移，禁止 `Base.metadata.create_all`。

---

## 验收与测试

> 注意：`docs/e2e_phase2-7.py` 的部分断言按**默认离线 Provider（mock）**的输出文本编写。
> 按「本机真实模型」一节接入 Qwen 后，涉及 LLM 回答内容的断言（如 phase3/4/5 的 mock 标注检查）不再适用——
> 需要回归验收时，把 `.env` 的 `LLM_PROVIDER` 临时切回 `mock` 再跑即可。pytest 单测不依赖运行时 Provider 配置，始终有效。

```powershell
# 后端单元测试（安全、模型元数据、RAG 解析/切片/Provider/检索、SQLite 级联完整性、嵌入管线）
cd backend
python -m pytest -q                     # 127 passed（DATABASE_URL 指向已迁移的库；本机没有 PostgreSQL 时，MCP 的 knowledge_search 用例会因连不上 PG 而失败，其余照常）

# Phase 2 端到端验收（需后端已启动）
cd ..
python docs/e2e_phase2.py               # 14 组断言：CRUD / TXT+DOCX+PDF 上传解析 / 越权 / 任务 / Dashboard / 级联 / 清理
python docs/e2e_phase2.py --pdf "任意真实.pdf"   # 额外用真实 PDF 跑一遍解析

# Phase 3 端到端验收（需后端已启动）：上传→嵌入→检索→问答→重建向量→越权
python docs/e2e_phase3.py

# Phase 4 端到端验收：会话 CRUD→SSE 流式对话→RAG 引用→历史→Dashboard 计数
python docs/e2e_phase4.py

# Phase 5 端到端验收：Agent CRUD→工具调用→数据分析出图→计算器→知识检索
python docs/e2e_phase5.py

# Phase 6 端到端验收：MCP 协议握手→发现→测试调用→Agent 走 mcp_calculator
python docs/e2e_phase6.py

# Phase 7 端到端验收：model-service 四端点 + 平台加固后错误形状
python docs/e2e_phase7.py

python docs/verify_cascade.py --db backend/ai_platform.db --dsn sqlite:///./ai_platform.db

# 解析管线（不需要服务）
python docs/verify_phase2_pipeline.py

# 真实本地模型（Qwen3.5-0.8B）：模型服务契约 + 流式 + 工具调用 + 平台 SSE/RAG 引用
python docs/verify_local_llm.py

# 真实 Celery worker 消费队列（本机没有 Redis/Docker 时可用 SQLAlchemy broker 起一个真 worker，
# 步骤见 docs/verify_async_worker.py 头部注释）
python docs/verify_async_worker.py

# 前端类型检查 + 构建
cd frontend
npm run build
```

`docs/` 目录说明：

| 文件 | 用途 |
| --- | --- |
| `e2e_phase2.py` | Phase 2 端到端验收（对着运行中的后端跑） |
| `e2e_phase3.py` | Phase 3 端到端验收：上传→嵌入→向量检索→RAG 问答→重建向量→越权隔离 |
| `e2e_phase4.py` | Phase 4 端到端验收：会话 CRUD→SSE 流式→RAG 引用→历史持久化→Dashboard |
| `e2e_phase5.py` | Phase 5 端到端验收：Agent CRUD→数据分析出图→计算器→知识检索→步骤日志 |
| `e2e_phase6.py` | Phase 6 端到端验收：MCP 协议→发现→测试调用→Agent 走 MCP 工具 |
| `e2e_phase7.py` | Phase 7 端到端验收：model-service 四端点契约（含 SSE） |
| `verify_cascade.py` | 级联删除验收：直接核对数据库行，证明删除知识库不会留下孤儿数据 |
| `verify_phase2_pipeline.py` | 解析 → 清洗 → 切片管线验证（TXT / DOCX、中文与偏移） |
| `verify_async_worker.py` | 真实 Celery worker 进程消费队列的验收（传输层可用 SQLAlchemy broker 替代 Redis） |
| `verify_local_llm.py` | 真实本地模型（Qwen3.5-0.8B）验收：模型服务契约 / SSE 流式 / 工具调用 + 平台 SSE 对话 / RAG 引用 |
| `_sampling_sweep.py` | Qwen3.5-0.8B 采样配置对比（温度 / top_k / 思考模式 → RAG 答案可用率） |
| `smoke_test.py` | Phase 1 认证与 Dashboard 冒烟测试 |
| `verify_utf8.py`、`_check_*.py`、`_check_*.js`、`tsconfig.check.json` | 开发期一次性排查脚本（编码、OpenAPI、语法平衡等） |
| `_extract_pdf.py`、`_dump_db.py` | 辅助工具：从 PDF 抽取文本、导出本地库内容 |
| `框架原文.txt` | 顶层需求 PDF 的文本抽取，便于与实现逐条对照 |

---

## 开发规范（继承自《项目开发框架》）

- **分层**：API / Service / Repository / Model；业务逻辑落在 `services/`，路由层不拼 SQL，Repository 不做事务提交。
- **配置**：所有密钥、DSN 经 `.env` 注入，禁止写死；`SECRET_KEY` 至少 32 字符。
- **迁移**：数据库结构变更一律走 Alembic。
- **API**：FastAPI 自动生成 OpenAPI（`/api/openapi.json`）& Swagger（`/api/docs`）。
- **异步**：文档解析等耗时任务走 Celery + Redis，不阻塞 FastAPI 主线程；没有 broker 时安全降级为内联执行。
- **一致性**：PostgreSQL 与本地 SQLite 预览的行为必须一致（例如外键级联需显式开启 SQLite 的 `PRAGMA foreign_keys`）。
- **测试**：核心逻辑使用 pytest；每个 Phase 结束都要跑通端到端验收脚本。
- **每个 Phase 必须保证可运行**，并同步更新 README 与运行说明。

---

## 演示流程（约 5 分钟，对齐《项目开发框架》第十六节）

1. 注册登录 → 知识库页新建库、上传 PDF（观察解析+向量化进度）
2. 对话页绑定该知识库提问 → 流式答案 + 来源引用
3. Agent 页新建 Agent、上传 Excel → 对话绑定 Agent 问"分析各月销售趋势并找出异常月份" → 步骤时间线 + 图表 + 结论
4. MCP 工具页查看 5 个已发现工具、测试面板直接调用 weather
5. 异步任务页查看任务记录 → Dashboard 看统计与调用趋势

## 后续可选扩展

- 对接真实厂商模型（.env 三件套即可）；本机真实模型已接入 Qwen3.5-0.8B（见「本机真实模型」），
  可继续换更强权重、把 Embedding/Reranker 也换成真实模型、或补 CUDA 版 torch 提速
- WSL2 之外的部署形态（K8s Compose 迁移）、pgvector 部分索引与分库策略
- conversations 的分页与消息截断、会话标题自动摘要

