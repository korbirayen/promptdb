from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional


HERE = Path(__file__).resolve().parent
DEFAULT_DB_PATH = HERE / "prompts.sqlite"
WEB_DIR = HERE / "web"


@dataclass(frozen=True)
class ServerConfig:
    db_path: Path


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def clamp_int(value: Optional[str], default: int, min_v: int, max_v: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(min_v, min(max_v, parsed))


def safe_join(base: Path, relative: str) -> Optional[Path]:
    rel = relative.lstrip("/")
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        return None
    return candidate


class PromptDBHandler(BaseHTTPRequestHandler):
    server_version = "promptdb/0.1"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/health":
            return self._send_json({"ok": True, "time": utc_iso()})

        if path == "/api/stats":
            return self._handle_stats()

        if path == "/api/sources":
            return self._handle_sources()

        if path == "/api/prompts":
            return self._handle_list_prompts(qs)

        match = re.fullmatch(r"/api/prompts/(\d+)", path)
        if match:
            prompt_id = int(match.group(1))
            return self._handle_get_prompt(prompt_id)

        # Static files
        if path == "/":
            return self._serve_static("index.html")
        if path == "/app.js":
            return self._serve_static("app.js")
        if path == "/styles.css":
            return self._serve_static("styles.css")

        # Default: try to serve any file under web/ (MVP convenience)
        if path.startswith("/"):
            rel = path[1:]
            return self._serve_static(rel)

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: Any) -> None:
        # Keep logs readable
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format % args))

    @property
    def cfg(self) -> ServerConfig:
        return self.server.cfg  # type: ignore[attr-defined]

    def _send_bytes(self, body: bytes, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        self._send_bytes(json_bytes(payload), status, "application/json; charset=utf-8")

    def _serve_static(self, rel: str) -> None:
        path = safe_join(WEB_DIR, rel)
        if path is None or not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        ext = path.suffix.lower()
        if ext == ".html":
            ctype = "text/html; charset=utf-8"
        elif ext == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif ext == ".css":
            ctype = "text/css; charset=utf-8"
        elif ext == ".svg":
            ctype = "image/svg+xml"
        else:
            ctype = "application/octet-stream"

        body = path.read_bytes()
        self._send_bytes(body, 200, ctype)

    def _handle_stats(self) -> None:
        if not self.cfg.db_path.exists():
            return self._send_json({"error": "DB not found", "db_path": str(self.cfg.db_path)}, status=404)

        with open_db(self.cfg.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM prompts").fetchone()["n"]
            by_source = conn.execute(
                """
                SELECT source, COALESCE(source_repo, '') AS repo, COUNT(*) AS n
                FROM prompts
                GROUP BY source, COALESCE(source_repo, '')
                ORDER BY n DESC
                """
            ).fetchall()

        return self._send_json(
            {
                "total": int(total),
                "by_source": [{"source": r["source"], "repo": r["repo"], "count": int(r["n"])} for r in by_source],
            }
        )

    def _handle_sources(self) -> None:
        if not self.cfg.db_path.exists():
            return self._send_json({"error": "DB not found", "db_path": str(self.cfg.db_path)}, status=404)

        with open_db(self.cfg.db_path) as conn:
            rows = conn.execute(
                """
                SELECT source, COALESCE(source_repo, '') AS repo, COUNT(*) AS n
                FROM prompts
                GROUP BY source, COALESCE(source_repo, '')
                ORDER BY n DESC
                """
            ).fetchall()

        items = [{"source": r["source"], "repo": r["repo"], "count": int(r["n"])} for r in rows]
        return self._send_json({"items": items})

    def _handle_list_prompts(self, qs: dict[str, list[str]]) -> None:
        if not self.cfg.db_path.exists():
            return self._send_json({"error": "DB not found", "db_path": str(self.cfg.db_path)}, status=404)

        query = (qs.get("q", [""])[0] or "").strip()
        source = (qs.get("source", [""])[0] or "").strip()
        repo = (qs.get("repo", [""])[0] or "").strip()
        limit = clamp_int(qs.get("limit", [None])[0], default=50, min_v=1, max_v=200)
        offset = clamp_int(qs.get("offset", [None])[0], default=0, min_v=0, max_v=1_000_000)

        clauses: list[str] = []
        params: list[Any] = []
        if query:
            clauses.append("(title LIKE ? OR body LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])
        if source:
            clauses.append("source = ?")
            params.append(source)
        if repo:
            clauses.append("COALESCE(source_repo, '') = ?")
            params.append(repo)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        sql = f"""
            SELECT id, title, source, source_repo
            FROM prompts
            {where}
            ORDER BY title COLLATE NOCASE ASC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with open_db(self.cfg.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
            total_sql = f"SELECT COUNT(*) AS n FROM prompts {where}"
            total = conn.execute(total_sql, params[:-2]).fetchone()["n"]

        items = [
            {
                "id": int(r["id"]),
                "title": r["title"],
                "source": r["source"],
                "source_repo": r["source_repo"],
            }
            for r in rows
        ]

        return self._send_json(
            {
                "items": items,
                "total": int(total),
                "limit": limit,
                "offset": offset,
                "q": query,
            }
        )

    def _handle_get_prompt(self, prompt_id: int) -> None:
        if not self.cfg.db_path.exists():
            return self._send_json({"error": "DB not found", "db_path": str(self.cfg.db_path)}, status=404)

        with open_db(self.cfg.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, title, body, source, source_repo, source_path
                FROM prompts
                WHERE id = ?
                """,
                (prompt_id,),
            ).fetchone()

        if row is None:
            return self._send_json({"error": "Not found", "id": prompt_id}, status=404)

        return self._send_json(
            {
                "id": int(row["id"]),
                "title": row["title"],
                "body": row["body"],
                "source": row["source"],
                "source_repo": row["source_repo"],
                "source_path": row["source_path"],
            }
        )


class PromptDBHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], RequestHandlerClass, cfg: ServerConfig):
        super().__init__(server_address, RequestHandlerClass)
        self.cfg = cfg


def main() -> int:
    host = os.environ.get("PROMPTDB_HOST", "127.0.0.1")
    port = int(os.environ.get("PROMPTDB_PORT", "7070"))
    db_path = Path(os.environ.get("PROMPTDB_DB", str(DEFAULT_DB_PATH))).expanduser().resolve()

    cfg = ServerConfig(db_path=db_path)
    httpd = PromptDBHTTPServer((host, port), PromptDBHandler, cfg)

    print(f"promptdb server running: http://{host}:{port}")
    print(f"db: {db_path}")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
