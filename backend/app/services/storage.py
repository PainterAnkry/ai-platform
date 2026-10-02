"""Upload storage helpers shared by the document and knowledge base services.

Uploads live under ``settings.upload_dir`` as ``<user_id>/<random>.<ext>``; the
DB only stores that relative ``storage_path``. Both the "delete one document"
and "delete a whole knowledge base" paths need to map it back to a file on
disk, so the logic lives here once.
"""

from __future__ import annotations

import logging
import os

from app.core.config import settings

logger = logging.getLogger(__name__)


def absolute_storage_path(storage_path: str) -> str:
    """Absolute path for a stored upload.

    An already-absolute ``storage_path`` is returned unchanged (``os.path.join``
    discards the base in that case), which keeps older rows working.
    """
    return os.path.join(settings.upload_dir, storage_path)


def remove_stored_file(storage_path: str) -> bool:
    """Best-effort delete of one stored upload.

    Returns ``True`` when a file was actually removed. A missing file is not an
    error — the DB row is the source of truth and the row is going away either
    way — so callers can ignore the result.
    """
    try:
        absolute = absolute_storage_path(storage_path)
        if os.path.isfile(absolute):
            os.remove(absolute)
            return True
    except OSError as exc:  # pragma: no cover - depends on the filesystem
        logger.warning("Could not remove stored file %s: %s", storage_path, exc)
    return False
