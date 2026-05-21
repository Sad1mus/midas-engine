"""Manage MIDAS Capital Architecture Decision Records (ADRs).

ADRs live in vault/10_Decisions/ as Markdown files with YAML frontmatter.
Each ADR is indexed in the SQLite decisions table and appended to the
audit_log hash chain.

Commands:
    new TITLE          Create a new ADR draft, open in $EDITOR
    list               List all ADRs with status
    show ID            Print an ADR (e.g. DEC-001)
    accept ID          Mark an ADR as accepted (writes to audit_log)
    supersede ID BY    Mark an ADR as superseded by another

Usage:
    python scripts/decision_log.py new "Adopt DuckDB for hot path"
    python scripts/decision_log.py list
    python scripts/decision_log.py show DEC-001
    python scripts/decision_log.py accept DEC-005
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.audit import chain  # noqa: E402
from infra.audit.chain import chain_step, sha256_hex  # noqa: E402  (re-exported)

VAULT_DIR = REPO_ROOT / "vault"
DECISIONS_DIR = VAULT_DIR / "10_Decisions"
DEFAULT_DB = REPO_ROOT / ".midas" / "midas.db"
CHAIN_PATH = chain.DEFAULT_CHAIN

DEC_FILENAME_RE = re.compile(r"^DEC-(\d{3})_(.+)\.md$")
ZERO_HASH = "0" * 64


# ─────────────────────────────────────────────────────────────────────
# ADR frontmatter handling
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ADRFrontmatter:
    id: str
    title: str
    status: str
    owner: str
    created: str
    type: str = "decision"
    superseded_by: str | None = None
    tags: list[str] | None = None

    def to_yaml(self) -> str:
        data: dict[str, object] = {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "status": self.status,
            "owner": self.owner,
            "created": self.created,
        }
        if self.superseded_by:
            data["superseded_by"] = self.superseded_by
        if self.tags:
            data["tags"] = self.tags
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split a Markdown file with YAML frontmatter into (meta, body)."""
    if not text.startswith("---\n"):
        raise ValueError("file does not start with YAML frontmatter (--- on line 1)")
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        raise ValueError("malformed frontmatter (need closing --- delimiter)")
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return meta, body


# ─────────────────────────────────────────────────────────────────────
# Audit log append
# ─────────────────────────────────────────────────────────────────────


def append_audit(
    conn: sqlite3.Connection,
    *,
    actor: str,
    event_type: str,
    event_key: str | None,
    payload: dict[str, object],
) -> int:
    """Append an entry to audit_log, maintaining the SHA-256 chain.

    Returns the new audit_log.id.
    """
    now = dt.datetime.now(dt.UTC)
    ts_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    ts_ms = int(now.timestamp() * 1000)

    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_hash = sha256_hex(payload_json)

    last = conn.execute("SELECT chain_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = last[0] if last else ZERO_HASH
    new_chain_hash = chain_step(prev_hash, payload_hash)

    cur = conn.execute(
        """
        INSERT INTO audit_log
            (ts_utc, ts_ms, actor, event_type, event_key, payload_json,
             payload_hash, prev_hash, chain_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts_iso,
            ts_ms,
            actor,
            event_type,
            event_key,
            payload_json,
            payload_hash,
            prev_hash,
            new_chain_hash,
        ),
    )
    conn.commit()
    audit_id = cur.lastrowid
    if audit_id is None:
        raise RuntimeError("failed to insert audit_log row")
    return audit_id


# ─────────────────────────────────────────────────────────────────────
# ADR discovery and ID allocation
# ─────────────────────────────────────────────────────────────────────


def existing_adr_ids() -> list[str]:
    """Return all existing ADR IDs sorted ascending."""
    ids: list[str] = []
    if not DECISIONS_DIR.exists():
        return ids
    for path in DECISIONS_DIR.glob("DEC-*.md"):
        m = DEC_FILENAME_RE.match(path.name)
        if m:
            ids.append(f"DEC-{m.group(1)}")
    ids.sort()
    return ids


def next_adr_id() -> str:
    """Allocate the next sequential ADR id (DEC-NNN)."""
    ids = existing_adr_ids()
    if not ids:
        return "DEC-001"
    last_num = int(ids[-1].split("-")[1])
    return f"DEC-{last_num + 1:03d}"


def slugify(title: str) -> str:
    """Convert a human title to a filename-safe slug."""
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", title)
    s = re.sub(r"\s+", "-", s.strip())
    return s


def adr_path(adr_id: str, title: str) -> Path:
    """Return the canonical filesystem path for an ADR."""
    return DECISIONS_DIR / f"{adr_id}_{slugify(title)}.md"


def find_adr(adr_id: str) -> Path:
    """Find an ADR file by ID, returning its path."""
    if not DECISIONS_DIR.exists():
        raise FileNotFoundError(f"vault decisions directory does not exist: {DECISIONS_DIR}")
    matches = list(DECISIONS_DIR.glob(f"{adr_id}_*.md"))
    if not matches:
        raise FileNotFoundError(f"no ADR found with id {adr_id}")
    if len(matches) > 1:
        raise RuntimeError(f"multiple files for {adr_id}: {matches}")
    return matches[0]


# ─────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────


def cmd_new(title: str, *, db_path: Path, owner: str | None = None) -> int:
    """Create a new ADR draft."""
    adr_id = next_adr_id()
    now = dt.datetime.now(dt.UTC)
    created = now.date().isoformat()
    owner_final = owner or os.environ.get("USER", "unknown")

    fm = ADRFrontmatter(
        id=adr_id,
        title=title,
        status="proposed",
        owner=owner_final,
        created=created,
    )

    body = f"""# {adr_id}: {title}

## Context

_What is the situation that requires a decision?_

## Decision

_What we decide. Be concrete._

## Consequences

### Positive

_What becomes easier or possible._

### Negative / Trade-offs

_What becomes harder or costlier._

### Risks

_What could go wrong, and how we mitigate._

## Alternatives Considered

_Other options and why they were rejected._

## References

_Links, sources, related ADRs._
"""

    content = f"---\n{fm.to_yaml()}---\n\n{body}"
    path = adr_path(adr_id, title)
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    content_hash = sha256_hex(body)

    # 1. Append to the portable chain (the source of truth).
    event = chain.append_event(
        actor=f"human:{owner_final}",
        event_type="decision",
        event_key=adr_id,
        payload={
            "action": "create",
            "id": adr_id,
            "title": title,
            "status": "proposed",
            "owner": owner_final,
            "vault_path": str(path.relative_to(VAULT_DIR)),
            "content_hash": content_hash,
        },
        chain_path=CHAIN_PATH,
    )

    # 2. Project that event into the SQLite mirror.
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        audit_id = chain.project_event(conn, event)
        conn.commit()
    finally:
        conn.close()

    try:
        display = path.relative_to(REPO_ROOT)
    except ValueError:
        display = path  # path is outside repo root (e.g. in a test tmp dir)
    print(f"created {adr_id} at {display}")
    print(f"audit_id: {audit_id}  content_hash: {content_hash[:12]}...")
    return 0


def cmd_list(*, db_path: Path) -> int:
    """List all ADRs with their status."""
    if not db_path.exists():
        print(f"db does not exist yet: {db_path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title, status, owner, created_utc FROM decisions ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("(no decisions registered)")
        return 0

    print(f"{'ID':<8} {'Status':<11} {'Owner':<15} {'Created':<11} Title")
    print("-" * 100)
    for row in rows:
        adr_id, title, status, owner, created = row
        created_date = created.split("T")[0] if "T" in created else created
        print(f"{adr_id:<8} {status:<11} {owner:<15} {created_date:<11} {title}")
    return 0


def cmd_show(adr_id: str) -> int:
    """Print an ADR to stdout."""
    try:
        path = find_adr(adr_id)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_accept(adr_id: str, *, db_path: Path, owner: str | None = None) -> int:
    """Mark an ADR as accepted."""
    try:
        path = find_adr(adr_id)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    text = path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(text)
    if meta.get("status") == "accepted":
        print(f"{adr_id} already accepted")
        return 0

    meta["status"] = "accepted"
    new_text = f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)}---\n\n{body}"
    path.write_text(new_text, encoding="utf-8")

    content_hash = sha256_hex(body)
    owner_final = owner or os.environ.get("USER", "unknown")

    # 1. Append the accept event to the portable chain (source of truth).
    event = chain.append_event(
        actor=f"human:{owner_final}",
        event_type="decision",
        event_key=adr_id,
        payload={
            "action": "accept",
            "id": adr_id,
            "content_hash": content_hash,
        },
        chain_path=CHAIN_PATH,
    )

    # 2. Project into the SQLite mirror.
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        audit_id = chain.project_event(conn, event)
        conn.commit()
    finally:
        conn.close()

    print(f"accepted {adr_id} (audit_id: {audit_id})")
    return 0


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="MIDAS Capital ADR / decision log manager")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--owner", type=str, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="create a new ADR draft")
    p_new.add_argument("title", type=str, help="ADR title (will be slugified)")

    sub.add_parser("list", help="list all ADRs")

    p_show = sub.add_parser("show", help="print an ADR by id")
    p_show.add_argument("id", type=str)

    p_accept = sub.add_parser("accept", help="mark an ADR as accepted")
    p_accept.add_argument("id", type=str)

    args = parser.parse_args()

    if args.cmd == "new":
        return cmd_new(args.title, db_path=args.db, owner=args.owner)
    if args.cmd == "list":
        return cmd_list(db_path=args.db)
    if args.cmd == "show":
        return cmd_show(args.id)
    if args.cmd == "accept":
        return cmd_accept(args.id, db_path=args.db, owner=args.owner)
    return 1


if __name__ == "__main__":
    sys.exit(main())
