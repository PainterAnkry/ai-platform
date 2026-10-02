"""Acceptance check: deleting a knowledge base must cascade in *this* database.

The API relies on database-level ``ON DELETE CASCADE`` (documents -> chunks and
tasks). PostgreSQL enforces that by default; SQLite only does when
``PRAGMA foreign_keys = ON`` is set on every connection. This script uploads a
document, deletes the *knowledge base* (not the document), and then inspects the
database directly for rows that should have cascaded away.

Row counts are scoped to the knowledge base under test, so a database that still
carries orphans from earlier runs cannot mask the result.

Usage (backend running, SQLite preview DB):
    python docs/verify_cascade.py --db backend/ai_platform.db --dsn sqlite:///./ai_platform.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000/api/v1"


def call(method: str, path: str, body=None, token: str | None = None,
         raw: bytes | None = None, filename: str | None = None):
    headers: dict[str, str] = {}
    data = None
    if raw is not None:
        boundary = "----cascade" + uuid.uuid4().hex
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        data = head + raw + f"\r\n--{boundary}--\r\n".encode("utf-8")
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read().decode()
            return resp.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        try:
            return exc.code, json.loads(payload)
        except ValueError:
            return exc.code, payload


def scoped_counts(conn: sqlite3.Connection, kb_id: int, doc_id: int) -> dict[str, int]:
    """Rows that must vanish with the knowledge base."""
    one = lambda sql, *params: int(conn.execute(sql, params).fetchone()[0])  # noqa: E731
    return {
        "knowledge_bases": one("select count(*) from knowledge_bases where id = ?", kb_id),
        "documents": one("select count(*) from documents where knowledge_base_id = ?", kb_id),
        "document_chunks": one(
            "select count(*) from document_chunks where knowledge_base_id = ?", kb_id
        ),
        "tasks": one("select count(*) from tasks where document_id = ?", doc_id),
    }


def orphan_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Database-wide integrity probe (any pre-existing orphans are reported)."""
    one = lambda sql: int(conn.execute(sql).fetchone()[0])  # noqa: E731
    return {
        "orphan_documents": one(
            "select count(*) from documents d "
            "left join knowledge_bases k on k.id = d.knowledge_base_id where k.id is null"
        ),
        "orphan_chunks": one(
            "select count(*) from document_chunks c "
            "left join documents d on d.id = c.document_id where d.id is null"
        ),
        "orphan_tasks": one(
            "select count(*) from tasks t "
            "left join documents d on d.id = t.document_id "
            "where t.document_id is not null and d.id is null"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="backend/ai_platform.db",
                        help="SQLite file used by the running backend")
    parser.add_argument("--dsn", default="sqlite:///./ai_platform.db",
                        help="DSN the backend uses; a non-SQLite DSN skips the row inspection")
    args = parser.parse_args()
    is_sqlite = not args.dsn or args.dsn.startswith("sqlite")

    user = "casc" + uuid.uuid4().hex[:8]
    password = "cascadepass123"
    st, _ = call("POST", "/auth/register",
                 {"username": user, "email": f"{user}@example.com", "password": password})
    assert st == 201, st
    st, tok = call("POST", "/auth/login", {"username": user, "password": password})
    token = tok["access_token"]

    st, kb = call("POST", "/knowledge-bases", {"name": "级联测试库"}, token=token)
    assert st == 201, st
    kb_id = kb["id"]

    st, up = call("POST", f"/knowledge-bases/{kb_id}/documents",
                  raw=("级联删除回归测试。" * 30).encode("utf-8"),
                  filename="cascade.txt", token=token)
    assert st == 201, f"{st} {up}"
    doc_id = up["document"]["id"]
    print(f"  upload -> kb id={kb_id} document id={doc_id} "
          f"status={up['document']['status']} chunks={up['document']['chunk_count']} "
          f"dispatch={up['dispatch']}")

    failures: list[str] = []
    conn = sqlite3.connect(args.db) if is_sqlite else None
    try:
        if conn is not None:
            before = scoped_counts(conn, kb_id, doc_id)
            print(f"  rows before delete (scoped): {before}")
            if before["documents"] != 1 or before["tasks"] != 1:
                failures.append(f"unexpected seed state: {before}")

        st, _ = call("DELETE", f"/knowledge-bases/{kb_id}", token=token)
        assert st == 204, st
        print("  DELETE /knowledge-bases/{id} -> 204")

        st, _ = call("GET", f"/knowledge-bases/{kb_id}", token=token)
        if st != 404:
            failures.append(f"knowledge base still readable ({st})")

        if conn is not None:
            after = scoped_counts(conn, kb_id, doc_id)
            print(f"  rows after delete  (scoped): {after}")
            for table, count in after.items():
                if count != 0:
                    failures.append(f"{table}: {count} row(s) survived the cascade")
            orphans = orphan_counts(conn)
            print(f"  database-wide orphans: {orphans}")
            if any(orphans.values()):
                failures.append(f"database holds orphan rows: {orphans}")
    finally:
        if conn is not None:
            conn.close()

    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print("   -", f)
        return 1
    print("RESULT: cascade verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
