from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional


TEXT_EXTS = {
    ".md",
    ".txt",
    ".prompt",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
}

SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".svelte-kit",
    ".turbo",
    ".vercel",
    "coverage",
}

SKIP_FILE_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}

MAX_FILE_BYTES = 512 * 1024  # keep it predictable; can be raised later


@dataclass(frozen=True)
class PromptRow:
    title: str
    body: str
    source: str
    source_repo: Optional[str]
    source_path: Optional[str]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_text_file(path: Path) -> str:
    # Try UTF-8 first; fall back to cp1252/latin-1 style decodes without blowing up.
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")


def infer_title_from_markdown(text: str) -> Optional[str]:
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _MD_HEADING_RE.match(line)
        if match:
            return match.group(1).strip().strip("`*")
        break
    return None


_FENCE_RE = re.compile(
    r"```(?P<lang>[a-zA-Z0-9_-]+)?\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)


def extract_best_fenced_block(text: str) -> Optional[str]:
    blocks = []
    for match in _FENCE_RE.finditer(text):
        body = match.group("body")
        if body:
            blocks.append(body.strip("\n"))
    if not blocks:
        return None
    # Heuristic: the longest fenced block is usually the actual prompt
    return max(blocks, key=len).strip()


def iter_repo_text_files(repo_root: Path) -> Iterator[Path]:
    for root, dirs, files in os.walk(repo_root):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
        for name in files:
            if name in SKIP_FILE_NAMES:
                continue
            path = root_path / name
            if path.suffix.lower() not in TEXT_EXTS:
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path


def load_fabric_patterns(patterns_root: Path, *, origin: str) -> Iterator[PromptRow]:
    # Fabric patterns are folders containing system.md/user.md
    if not patterns_root.exists():
        return

    for pattern_dir in sorted([p for p in patterns_root.iterdir() if p.is_dir()]):
        title = pattern_dir.name
        system_path = pattern_dir / "system.md"
        user_path = pattern_dir / "user.md"
        parts: list[str] = []

        if system_path.exists():
            parts.append("# system\n" + read_text_file(system_path).strip() + "\n")
        if user_path.exists():
            parts.append("# user\n" + read_text_file(user_path).strip() + "\n")

        body = "\n".join([p for p in parts if p.strip()]).strip()
        if not body:
            continue

        rel = str(pattern_dir.relative_to(patterns_root)).replace("\\", "/")
        source_path = f"{origin}/{rel}".replace("//", "/")

        yield PromptRow(
            title=title,
            body=body,
            source="patterns",
            source_repo=None,
            source_path=source_path,
        )


def load_awesome_chatgpt_prompts_csv(repo_root: Path) -> Iterator[PromptRow]:
    # Repo structure here is nested: awesome-chatgpt-prompts-main/awesome-chatgpt-prompts-main/prompts.csv
    csv_path = repo_root / "awesome-chatgpt-prompts-main" / "prompts.csv"
    if not csv_path.exists():
        return

    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=1):
            title = (row.get("act") or "").strip()
            body = (row.get("prompt") or "").strip()
            if not title or not body:
                continue
            yield PromptRow(
                title=title,
                body=body,
                source="repo_csv",
                source_repo="awesome-chatgpt-prompts-main",
                source_path=f"prompts.csv#row={idx}",
            )


def load_repo_files_as_prompts(repo_name: str, repo_root: Path) -> Iterator[PromptRow]:
    for path in iter_repo_text_files(repo_root):
        rel = str(path.relative_to(repo_root)).replace("\\", "/")
        text = read_text_file(path).strip()
        if not text:
            continue

        title = infer_title_from_markdown(text) or path.stem
        fenced = extract_best_fenced_block(text)
        body = fenced.strip() if fenced and len(fenced) >= 40 else text

        yield PromptRow(
            title=title,
            body=body,
            source="repo_file",
            source_repo=repo_name,
            source_path=rel,
        )


def load_strategies(strategies_dir: Path, *, origin: str) -> Iterator[PromptRow]:
    if not strategies_dir.exists():
        return

    for path in sorted(strategies_dir.glob("*.json")):
        text = read_text_file(path).strip()
        if not text:
            continue
        yield PromptRow(
            title=path.stem,
            body=text,
            source="strategies",
            source_repo=None,
            source_path=f"{origin}/{path.name}",
        )


def ensure_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(schema_path.read_text(encoding="utf-8"))


def insert_prompt(conn: sqlite3.Connection, pr: PromptRow, imported_at: str) -> bool:
    body_hash = sha256_text(pr.body)
    try:
        conn.execute(
            """
            INSERT INTO prompts(title, body, source, source_repo, source_path, body_sha256, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (pr.title, pr.body, pr.source, pr.source_repo, pr.source_path, body_hash, imported_at),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def export_web_bundle(conn: sqlite3.Connection, web_dir: Path, imported_at: str) -> Path:
    web_dir.mkdir(parents=True, exist_ok=True)

    rows = conn.execute(
        """
        SELECT
          id,
          title,
          body,
          source,
          COALESCE(source_repo, '') AS source_repo,
          COALESCE(source_path, '') AS source_path
        FROM prompts
        ORDER BY title COLLATE NOCASE ASC
        """
    ).fetchall()

    items = [
        {
            "id": int(r["id"]),
            "title": r["title"],
            "body": r["body"],
            "source": r["source"],
            "source_repo": r["source_repo"],
            "source_path": r["source_path"],
        }
        for r in rows
    ]

    src_rows = conn.execute(
        """
        SELECT source, COALESCE(source_repo, '') AS repo, COUNT(*) AS n
        FROM prompts
        GROUP BY source, COALESCE(source_repo, '')
        ORDER BY n DESC
        """
    ).fetchall()
    sources = [{"source": r["source"], "repo": r["repo"], "count": int(r["n"])} for r in src_rows]

    bundle = {
        "generated_at": imported_at,
        "total": len(items),
        "sources": sources,
        "items": items,
    }

    payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    out_path = web_dir / "data.js"
    out_path.write_text(
        "// Generated by import_prompts.py. Do not hand-edit.\n" + f"window.PROMPTDB={payload};\n",
        encoding="utf-8",
    )
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Import prompts into a unified SQLite DB")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Project root")
    ap.add_argument("--db", type=Path, default=Path("promptdb/prompts.sqlite"), help="Output SQLite file")
    ap.add_argument("--reset", action="store_true", help="Delete existing DB first")
    ap.add_argument(
        "--export-web",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write promptdb/web/data.js so the UI can run offline (default: on)",
    )
    args = ap.parse_args()

    root: Path = args.root.resolve()
    db_path: Path = (root / args.db).resolve() if not args.db.is_absolute() else args.db
    schema_path = root / "promptdb" / "schema.sql"

    if args.reset and db_path.exists():
        db_path.unlink()

    db_path.parent.mkdir(parents=True, exist_ok=True)

    imported_at = now_iso()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn, schema_path)

        counts: dict[str, int] = {}

        # 1) Patterns from this repo
        patterns_roots = [
            (root / "promptdb" / "patterns", "promptdb/patterns"),
            (root / "data" / "patterns", "data/patterns"),
        ]
        for patterns_root, origin in patterns_roots:
            for pr in load_fabric_patterns(patterns_root, origin=origin):
                if insert_prompt(conn, pr, imported_at):
                    counts[pr.source] = counts.get(pr.source, 0) + 1

        # 1b) Strategies from this repo
        strategies_dir = root / "promptdb" / "strategies"
        for pr in load_strategies(strategies_dir, origin="promptdb/strategies"):
            if insert_prompt(conn, pr, imported_at):
                counts[pr.source] = counts.get(pr.source, 0) + 1

        # 2) Other repos
        repos_root = root / "Other-Usful-Prompt-Repos"
        if repos_root.exists():
            # Special-case: awesome-chatgpt-prompts CSV
            awesome_root = repos_root / "awesome-chatgpt-prompts-main"
            if awesome_root.exists():
                for pr in load_awesome_chatgpt_prompts_csv(awesome_root):
                    if insert_prompt(conn, pr, imported_at):
                        counts[f"{pr.source}:{pr.source_repo}"] = counts.get(f"{pr.source}:{pr.source_repo}", 0) + 1

            # Generic file walkers for each repo folder
            for repo_dir in sorted([p for p in repos_root.iterdir() if p.is_dir()]):
                repo_name = repo_dir.name
                # many of these have a nested same-name folder; prefer that if present
                nested = repo_dir / repo_name
                repo_root = nested if nested.exists() and nested.is_dir() else repo_dir

                # Skip the one we already handled via CSV; still allow file-based prompts too
                for pr in load_repo_files_as_prompts(repo_name, repo_root):
                    if insert_prompt(conn, pr, imported_at):
                        counts[f"{pr.source}:{pr.source_repo}"] = counts.get(f"{pr.source}:{pr.source_repo}", 0) + 1

        conn.commit()

        total = conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
        print(f"DB: {db_path}")
        print(f"Total prompts: {total}")
        for k in sorted(counts.keys()):
            print(f"  {k}: {counts[k]}")

        # quick sanity check
        bad = conn.execute(
            "SELECT COUNT(*) FROM prompts WHERE title IS NULL OR TRIM(title) = '' OR body IS NULL OR TRIM(body) = ''"
        ).fetchone()[0]
        print(f"Empty title/body rows: {bad}")

        if args.export_web:
            web_dir = root / "promptdb" / "web"
            out_path = export_web_bundle(conn, web_dir, imported_at)
            print(f"Web bundle: {out_path}")

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
