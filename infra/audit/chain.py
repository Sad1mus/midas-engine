"""Portable audit hash chain — append-only NDJSON as the single source of truth.

The MIDAS "portable mind": the SHA-256 hash chain lives in
``infra/audit/chain.ndjson`` (one JSON object per line, git-tracked,
human-diffable, append-only). Each line is chained to the previous one via
``chain_hash = SHA-256(prev_hash || payload_hash)``.

SQLite's ``audit_log`` (and the ``decisions`` index) are *projections* of this
file: they can be wiped and rebuilt at any time with :func:`replay_to_db`. The
text chain is the authority — delete the ``.db`` and the entire decision/audit
history still travels with the repository.

Design rules:
  * Append-only. We never rewrite or reorder lines.
  * Canonical JSON (sorted keys, no whitespace) so hashes are reproducible.
  * No new dependencies — stdlib only (json, hashlib, sqlite3).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CHAIN = REPO_ROOT / "infra" / "audit" / "chain.ndjson"
ZERO_HASH = "0" * 64


# ─────────────────────────────────────────────────────────────────────
# Hash primitives
# ─────────────────────────────────────────────────────────────────────


def sha256_hex(data: str | bytes) -> str:
    """Return SHA-256 hex digest (lowercase) of input."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def chain_step(prev_hash: str, payload_hash: str) -> str:
    """Compute the next chain hash from the previous chain hash and current payload."""
    if len(prev_hash) != 64 or len(payload_hash) != 64:
        raise ValueError("hashes must be 64 hex chars")
    return sha256_hex(prev_hash + payload_hash)


def canonical_json(payload: Any) -> str:
    """Deterministic JSON serialization (sorted keys, no whitespace)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


# ─────────────────────────────────────────────────────────────────────
# Reading / writing the NDJSON chain (the source of truth)
# ─────────────────────────────────────────────────────────────────────


def read_events(chain_path: Path = DEFAULT_CHAIN) -> list[dict[str, Any]]:
    """Read all events from the chain file in order. Empty/missing file → []."""
    path = Path(chain_path)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def _tip(chain_path: Path) -> tuple[int, str]:
    """Return (last_seq, last_chain_hash) of the chain, or (0, ZERO_HASH) if empty."""
    events = read_events(chain_path)
    if not events:
        return 0, ZERO_HASH
    last = events[-1]
    return int(last["seq"]), str(last["chain_hash"])


def append_event(
    *,
    actor: str,
    event_type: str,
    event_key: str | None,
    payload: dict[str, Any],
    chain_path: Path = DEFAULT_CHAIN,
) -> dict[str, Any]:
    """Append one event to the NDJSON chain, maintaining the SHA-256 chain.

    Returns the full event record (including the computed hashes and seq).
    """
    now = dt.datetime.now(dt.UTC)
    ts_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    ts_ms = int(now.timestamp() * 1000)

    payload_hash = sha256_hex(canonical_json(payload))
    last_seq, prev_hash = _tip(chain_path)
    new_chain_hash = chain_step(prev_hash, payload_hash)

    event: dict[str, Any] = {
        "seq": last_seq + 1,
        "ts_utc": ts_iso,
        "ts_ms": ts_ms,
        "actor": actor,
        "event_type": event_type,
        "event_key": event_key,
        "payload": payload,
        "payload_hash": payload_hash,
        "prev_hash": prev_hash,
        "chain_hash": new_chain_hash,
    }

    path = Path(chain_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(canonical_json(event) + "\n")
    return event


def verify_file(chain_path: Path = DEFAULT_CHAIN) -> tuple[bool, str]:
    """Validate the NDJSON chain's integrity end to end.

    Checks, for every line: seq is contiguous, payload_hash matches the payload,
    prev_hash links to the previous chain_hash, and chain_hash is well formed.
    Returns (ok, message).
    """
    events = read_events(chain_path)
    if not events:
        return True, "chain file empty (0 events). nothing to verify."

    prev_chain_hash = ZERO_HASH
    for i, ev in enumerate(events, start=1):
        seq = ev.get("seq")
        if seq != i:
            return False, f"FAIL: seq discontinuity at line {i} (got {seq!r})"

        recomputed_payload = sha256_hex(canonical_json(ev["payload"]))
        if recomputed_payload != ev["payload_hash"]:
            return False, f"FAIL seq {seq}: payload_hash mismatch"

        if ev["prev_hash"] != prev_chain_hash:
            return False, f"FAIL seq {seq}: prev_hash does not link to previous chain_hash"

        recomputed_chain = chain_step(ev["prev_hash"], ev["payload_hash"])
        if recomputed_chain != ev["chain_hash"]:
            return False, f"FAIL seq {seq}: chain_hash mismatch"

        prev_chain_hash = ev["chain_hash"]

    return True, f"chain file ok: {len(events)} events verified, tip = {prev_chain_hash[:16]}..."


# ─────────────────────────────────────────────────────────────────────
# Projection: rebuild SQLite from the chain (chain → DB, never the reverse)
# ─────────────────────────────────────────────────────────────────────


def _project_decision(
    conn: sqlite3.Connection, payload: dict[str, Any], event: dict[str, Any], audit_id: int
) -> None:
    """Apply a decision event to the ``decisions`` index table."""
    action = payload.get("action")
    adr_id = payload["id"]
    if action == "create":
        conn.execute(
            """
            INSERT OR REPLACE INTO decisions
                (id, title, status, owner, created_utc, created_ms,
                 vault_path, content_hash, audit_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                adr_id,
                payload["title"],
                payload.get("status", "proposed"),
                payload.get("owner", "unknown"),
                event["ts_utc"],
                event["ts_ms"],
                payload["vault_path"],
                payload["content_hash"],
                audit_id,
            ),
        )
    elif action == "accept":
        conn.execute(
            "UPDATE decisions SET status = 'accepted', audit_id = ? WHERE id = ?",
            (audit_id, adr_id),
        )
    elif action == "supersede":
        conn.execute(
            "UPDATE decisions SET status = 'superseded', superseded_by = ?, audit_id = ? WHERE id = ?",
            (payload.get("superseded_by"), audit_id, adr_id),
        )


def _project_ontology_rule(
    conn: sqlite3.Connection, payload: dict[str, Any], event: dict[str, Any], audit_id: int
) -> None:
    """Apply an ontology_rule event to the ``ontology_rules`` table.

    Only restrictive actions are accepted (veto | size_down) — a Magister rule can
    never approve. The DB CHECK enforces it too; this is defence in depth.
    """
    op = payload.get("op", "create")
    if op == "create":
        action = payload["action"]
        if action not in ("veto", "size_down"):
            raise ValueError(
                f"ontology rule action must restrict (veto|size_down), got {action!r}"
            )
        conn.execute(
            """
            INSERT OR REPLACE INTO ontology_rules
                (id, ts_utc, ts_ms, rule_type, instrument, window_spec,
                 action, params_json, audit_id, vault_path, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                event["ts_utc"],
                event["ts_ms"],
                payload["rule_type"],
                payload.get("instrument"),
                canonical_json(payload.get("window_spec") or {}),
                action,
                canonical_json(payload.get("params") or {}),
                audit_id,
                payload.get("vault_path"),
                int(payload.get("active", 1)),
            ),
        )
    elif op == "deactivate":
        conn.execute(
            "UPDATE ontology_rules SET active = 0, audit_id = ? WHERE id = ?",
            (audit_id, payload["id"]),
        )


def project_event(conn: sqlite3.Connection, event: dict[str, Any]) -> int:
    """Project a single chain event into SQLite. Returns the audit_log row id.

    Inserts the audit_log row verbatim (preserving the chain hashes) and applies
    any table-specific side effects (the decisions index, ontology rules).
    """
    payload_json = canonical_json(event["payload"])
    cur = conn.execute(
        """
        INSERT INTO audit_log
            (ts_utc, ts_ms, actor, event_type, event_key, payload_json,
             payload_hash, prev_hash, chain_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["ts_utc"],
            event["ts_ms"],
            event["actor"],
            event["event_type"],
            event["event_key"],
            payload_json,
            event["payload_hash"],
            event["prev_hash"],
            event["chain_hash"],
        ),
    )
    audit_id = cur.lastrowid
    if audit_id is None:
        raise RuntimeError("failed to insert audit_log row during projection")

    if event["event_type"] == "decision":
        _project_decision(conn, event["payload"], event, audit_id)
    elif event["event_type"] == "ontology_rule":
        _project_ontology_rule(conn, event["payload"], event, audit_id)
    return audit_id


def replay_to_db(conn: sqlite3.Connection, chain_path: Path = DEFAULT_CHAIN) -> int:
    """Wipe and rebuild the audit_log + decisions projections from the chain file.

    Returns the number of events replayed. The chain file is the authority; this
    is the operation that makes the mind portable (delete .db, replay, recover).
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM decisions")
    # ontology_rules only exists from schema v2 onward.
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("DELETE FROM ontology_rules")
    conn.execute("DELETE FROM audit_log")
    # sqlite_sequence only exists once an AUTOINCREMENT table has rows.
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'audit_log'")
    conn.execute("PRAGMA foreign_keys = ON")

    n = 0
    for event in read_events(chain_path):
        project_event(conn, event)
        n += 1
    conn.commit()
    return n
