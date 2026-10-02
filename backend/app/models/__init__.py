"""ORM models.

Importing this package ensures every model class is registered with the
SQLAlchemy ``Base.metadata`` before Alembic autogeneration runs.
"""

from app.models.agent import Agent, DataFile, Tool  # noqa: F401
from app.models.chat import Conversation, Message  # noqa: F401
from app.models.knowledge import (  # noqa: F401
    Document,
    DocumentChunk,
    KnowledgeBase,
    Task,
)
from app.models.user import User  # noqa: F401
