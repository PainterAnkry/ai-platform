"""Agent management, tool registry, data files and chart serving (Phase 5)."""

from __future__ import annotations

import os

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser, DBSession
from app.agent.sandbox import analysis_dir
from app.schemas.agent import (
    AgentCreate,
    AgentRead,
    AgentUpdate,
    DataFileRead,
    ToolRead,
)
from app.services.agent_service import AgentService, DataFileService

router = APIRouter(prefix="/agents", tags=["agents"])


# ---------- agents CRUD ----------
@router.get("", response_model=list[AgentRead], summary="List the caller's agents")
def list_agents(db: DBSession, current_user: CurrentUser) -> list[AgentRead]:
    return AgentService(db).list_for_user(current_user.id)


@router.post(
    "",
    response_model=AgentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an agent",
)
def create_agent(
    payload: AgentCreate, db: DBSession, current_user: CurrentUser
) -> AgentRead:
    return AgentService(db).create(payload, current_user.id)


@router.get("/tools", response_model=list[ToolRead], summary="List the tool registry")
def list_tools(db: DBSession, current_user: CurrentUser) -> list[ToolRead]:
    return AgentService(db).list_tools()


@router.get(
    "/data-files",
    response_model=list[DataFileRead],
    summary="List uploaded Excel/CSV data files",
)
def list_data_files(db: DBSession, current_user: CurrentUser) -> list[DataFileRead]:
    return DataFileService(db).list_for_user(current_user.id)


@router.post(
    "/data-files",
    response_model=DataFileRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an Excel/CSV file for the analysis agent",
)
async def upload_data_file(
    db: DBSession,
    current_user: CurrentUser,
    file: UploadFile = File(..., description="XLSX / XLS / CSV, max 25 MB"),
) -> DataFileRead:
    data = await file.read()
    return DataFileService(db).upload(
        user_id=current_user.id, filename=file.filename or "", data=data
    )


@router.delete(
    "/data-files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a data file",
)
def delete_data_file(
    file_id: int, db: DBSession, current_user: CurrentUser
) -> None:
    DataFileService(db).delete(file_id, current_user.id)


@router.get("/{agent_id}", response_model=AgentRead, summary="Fetch one agent")
def get_agent(agent_id: int, db: DBSession, current_user: CurrentUser) -> AgentRead:
    return AgentRead.model_validate(AgentService(db).get_for_user(agent_id, current_user.id))


@router.patch(
    "/{agent_id}",
    response_model=AgentRead,
    summary="Update an agent (partial)",
)
def update_agent(
    agent_id: int,
    payload: AgentUpdate,
    db: DBSession,
    current_user: CurrentUser,
) -> AgentRead:
    return AgentService(db).update(agent_id, payload, current_user.id)


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an agent",
)
def delete_agent(agent_id: int, db: DBSession, current_user: CurrentUser) -> None:
    AgentService(db).delete(agent_id, current_user.id)


# ---------- charts (unauthenticated, unguessable names) ----------
@router.get(
    "/charts/{filename}",
    summary="Serve a PNG chart produced by the analysis sandbox",
    include_in_schema=True,
)
def serve_chart(filename: str) -> FileResponse:
    """Charts live in the analysis dir with random 12-hex names; serving them
    without auth keeps ``<img>`` tags simple (the browser cannot attach
    Authorization headers). Path traversal is blocked by the basename check.
    """
    safe = os.path.basename(filename)
    if not safe.endswith(".png") or safe != filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid chart name"
        )
    path = os.path.join(analysis_dir(), safe)
    if not os.path.exists(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chart not found"
        )
    return FileResponse(path, media_type="image/png")
