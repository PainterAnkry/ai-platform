"""Knowledge base, document and chunk Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field

from app.schemas.common import ORMModel

# Upload limits shared with the router layer (Phase 2 upload endpoint).
MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".txt"})

# Same extensions without the leading dot, as stored in ``documents.file_type``.
ALLOWED_FILE_TYPES: tuple[str, ...] = tuple(sorted(e.lstrip(".") for e in ALLOWED_EXTENSIONS))

DocumentStatus = Literal["pending", "processing", "completed", "failed"]
FileType = Literal["pdf", "docx", "txt"]


class KnowledgeBaseCreate(ORMModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1024)


class KnowledgeBaseUpdate(ORMModel):
    """Partial update — only supplied fields are applied."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=1024)


class KnowledgeBaseRead(ORMModel):
    id: int
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # Not a column: the list endpoint fills it in from the repository count.
    document_count: int = Field(default=0, ge=0)


class DocumentCreate(ORMModel):
    """Input for a parsed upload; file validation happens in the router."""

    knowledge_base_id: int
    filename: str = Field(min_length=1, max_length=255)
    file_type: FileType
    file_size: int = Field(ge=0, le=MAX_UPLOAD_SIZE_BYTES)
    storage_path: str = Field(min_length=1, max_length=512)


class DocumentUpdate(ORMModel):
    """Progress/status fields written by the ingestion worker."""

    status: Optional[DocumentStatus] = None
    progress: Optional[int] = Field(default=None, ge=0, le=100)
    error_message: Optional[str] = Field(default=None, max_length=1024)
    page_count: Optional[int] = Field(default=None, ge=0)
    chunk_count: Optional[int] = Field(default=None, ge=0)
    character_count: Optional[int] = Field(default=None, ge=0)


class DocumentRead(ORMModel):
    id: int
    knowledge_base_id: int
    filename: str
    file_type: str
    file_size: int
    status: DocumentStatus
    progress: int
    error_message: Optional[str] = None
    page_count: Optional[int] = None
    chunk_count: int
    character_count: int
    # Phase 3 provenance: which provider produced the chunks' vectors
    # (e.g. "mock:1024"); None until the ingestion worker embedded them.
    embedding_model: Optional[str] = None
    created_at: datetime
    updated_at: datetime
