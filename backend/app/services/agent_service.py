"""Agent / data-file business logic (Phase 5)."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools import sync_tool_rows
from app.core.config import settings
from app.models.agent import Agent, DataFile, Tool
from app.schemas.agent import AgentCreate, AgentRead, AgentUpdate, DataFileRead, ToolRead

logger = logging.getLogger(__name__)

MAX_DATA_FILE_BYTES = 25 * 1024 * 1024


class AgentService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---------- agents CRUD ----------
    def create(self, payload: AgentCreate, user_id: int) -> AgentRead:
        self._validate_tools(payload.tools)
        agent = Agent(
            user_id=user_id,
            name=payload.name.strip(),
            system_prompt=payload.system_prompt or "",
            model=(payload.model or "").strip() or None,
            temperature=payload.temperature,
            tools=payload.tools,
        )
        self.db.add(agent)
        self.db.commit()
        self.db.refresh(agent)
        return AgentRead.model_validate(agent)

    def list_for_user(self, user_id: int) -> list[AgentRead]:
        stmt = (
            select(Agent)
            .where(Agent.user_id == user_id)
            .order_by(Agent.id.desc())
        )
        return [AgentRead.model_validate(a) for a in self.db.execute(stmt).scalars()]

    def get_for_user(self, agent_id: int, user_id: int) -> Agent:
        agent = self.db.get(Agent, agent_id)
        if agent is None or agent.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
            )
        return agent

    def find_optional(self, agent_id: Optional[int]) -> Optional[Agent]:
        """Fetch without ownership checks — chat fallback path."""
        if agent_id is None:
            return None
        return self.db.get(Agent, agent_id)

    def update(
        self, agent_id: int, payload: AgentUpdate, user_id: int
    ) -> AgentRead:
        agent = self.get_for_user(agent_id, user_id)
        if payload.tools is not None:
            self._validate_tools(payload.tools)
        fields: dict = {}
        if payload.name is not None:
            fields["name"] = payload.name.strip()
        if payload.system_prompt is not None:
            fields["system_prompt"] = payload.system_prompt
        if payload.model is not None:
            fields["model"] = payload.model.strip() or None
        if payload.temperature is not None:
            fields["temperature"] = payload.temperature
        if payload.tools is not None:
            fields["tools"] = payload.tools or None
        for key, value in fields.items():
            setattr(agent, key, value)
        self.db.commit()
        self.db.refresh(agent)
        return AgentRead.model_validate(agent)

    def delete(self, agent_id: int, user_id: int) -> None:
        agent = self.get_for_user(agent_id, user_id)
        self.db.delete(agent)
        self.db.commit()

    # ---------- tool registry ----------
    def list_tools(self) -> list[ToolRead]:
        sync_tool_rows(self.db)
        rows = self.db.execute(select(Tool).order_by(Tool.id)).scalars()
        return [ToolRead.model_validate(row) for row in rows]

    def _validate_tools(self, names: Optional[list[str]]) -> None:
        if not names:
            return
        from app.agent.tools import get_tool_registry

        known = set(get_tool_registry())
        unknown = [name for name in names if name not in known]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"未知工具: {', '.join(unknown)}",
            )


class DataFileService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_for_user(self, user_id: int) -> list[DataFileRead]:
        stmt = (
            select(DataFile)
            .where(DataFile.user_id == user_id)
            .order_by(DataFile.id.desc())
            .limit(100)
        )
        return [DataFileRead.model_validate(row) for row in self.db.execute(stmt).scalars()]

    def get_for_user(self, file_id: int, user_id: int) -> DataFile:
        row = self.db.get(DataFile, file_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Data file not found"
            )
        return row

    def upload(
        self, *, user_id: int, filename: str, data: bytes
    ) -> DataFileRead:
        safe_name = os.path.basename(filename or "").strip()
        if not safe_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename"
            )
        ext = os.path.splitext(safe_name)[1].lower()
        if ext not in {".xlsx", ".xls", ".csv"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="仅支持 .xlsx / .xls / .csv 数据文件",
            )
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file"
            )
        if len(data) > MAX_DATA_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="数据文件超过 25 MB 限制",
            )

        user_dir = os.path.join(settings.upload_dir, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        stored_name = f"data_{uuid.uuid4().hex}{ext}"
        absolute = os.path.join(user_dir, stored_name)
        with open(absolute, "wb") as fh:
            fh.write(data)

        # Best-effort schema snapshot; a parse failure here is not fatal —
        # the analysis tool will surface a precise error later.
        row_count = 0
        column_names: Optional[list[str]] = None
        try:
            import pandas as pd

            if ext == ".csv":
                frame = pd.read_csv(absolute, encoding="utf-8-sig", sep=None,
                                    engine="python")
            else:
                frame = pd.read_excel(absolute)
            row_count = int(len(frame))
            column_names = [str(c) for c in frame.columns]
        except Exception as exc:  # noqa: BLE001 - snapshot only
            logger.warning("Could not preview %s at upload: %s", safe_name, exc)

        row = DataFile(
            user_id=user_id,
            filename=safe_name,
            file_type=ext.lstrip("."),
            file_size=len(data),
            storage_path=os.path.join(str(user_id), stored_name),
            row_count=row_count,
            column_names=column_names,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return DataFileRead.model_validate(row)

    def delete(self, file_id: int, user_id: int) -> None:
        row = self.get_for_user(file_id, user_id)
        path = row.storage_path
        if not os.path.isabs(path):
            path = os.path.join(settings.upload_dir, path)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:  # noqa: BLE001 - row still goes
            logger.warning("Could not remove data file %s", path)
        self.db.delete(row)
        self.db.commit()
