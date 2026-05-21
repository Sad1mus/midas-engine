"""Verify the integrity of the MIDAS Capital audit_log hash chain.

Walks audit_log from id=1 onward and verifies that:
  1. Each row's payload_hash == SHA-256(payload_json).
  2. Each row's prev_hash == previous row's chain_hash (or zeros for id=1).
  3. Each row's chain_hash == SHA-256(prev_hash || payload_hash).

Exits 0 on success, 1 on any failure with a diagnostic.

Optional --check-frontmatter-only mode validates only that decision notes
in vault/10_Decisions/ have well-formed YAML frontmatter (used by
pre-commit hook before DB exists).

Usage:
    python scripts/verify_chain.py
    python scripts/verify_chain.py --db /tmp/test.db
    python scripts/verify_chain.py --check-frontmatter-only
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.audit import chain  # noqa: E402

DEFAULT_DB = REPO_ROOT / ".midas" / "midas.db"
DEFAULT_CHAIN = chain.DEFAULT_CHAIN
VAULT_DECISIONS = REPO_ROOT / "vault" / "10_Decisions"
ZERO_HASH = "0" * 64

REQUIRED_FRONTMATTER = {"id", "title", "type", "status", "owner", "created"}
VALID_STATUSES = {"proposed", "accepted", "superseded", "rejected"}
DEC_ID_RE = re.compile(r"^DEC-\d{3}$")


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def verify_chain(db_path: Path) -> int:
    """Walk the chain. Return 0 if valid, 1 otherwise."""
    if not db_path.exists():
        print(f"error: db not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, payload_json, payload_hash, prev_hash, chain_hash
            FROM audit_log
            ORDER BY id ASC
            """
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("chain is empty (0 entries). nothing to verify.")
        return 0

    prev_chain_hash = ZERO_HASH
    n_ok = 0
    for row in rows:
        row_id, payload_json, payload_hash, prev_hash, chain_hash = row

        # 1. payload_hash integrity
        recomputed_payload = sha256_hex(payload_json)
        if recomputed_payload != payload_hash:
            print(
                f"FAIL row {row_id}: payload_hash mismatch\n"
                f"  stored:     {payload_hash}\n"
                f"  recomputed: {recomputed_payload}",
                file=sys.stderr,
            )
            return 1

        # 2. prev_hash matches previous row's chain_hash
        if prev_hash != prev_chain_hash:
            print(
                f"FAIL row {row_id}: prev_hash does not match previous chain_hash\n"
                f"  expected: {prev_chain_hash}\n"
                f"  got:      {prev_hash}",
                file=sys.stderr,
            )
            return 1

        # 3. chain_hash = SHA-256(prev_hash || payload_hash)
        recomputed_chain = sha256_hex(prev_hash + payload_hash)
        if recomputed_chain != chain_hash:
            print(
                f"FAIL row {row_id}: chain_hash mismatch\n"
                f"  stored:     {chain_hash}\n"
                f"  recomputed: {recomputed_chain}",
                file=sys.stderr,
            )
            return 1

        prev_chain_hash = chain_hash
        n_ok += 1

    print(f"chain ok: {n_ok} entries verified, tip = {prev_chain_hash[:16]}...")
    return 0


def verify_frontmatter() -> int:
    """Validate that every ADR has well-formed frontmatter. Used by pre-commit."""
    if not VAULT_DECISIONS.exists():
        return 0

    errors: list[str] = []
    for path in sorted(VAULT_DECISIONS.glob("DEC-*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            errors.append(f"{path.name}: missing YAML frontmatter")
            continue
        parts = text.split("---\n", 2)
        if len(parts) < 3:
            errors.append(f"{path.name}: malformed frontmatter delimiters")
            continue
        try:
            meta = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError as e:
            errors.append(f"{path.name}: YAML parse error: {e}")
            continue

        missing = REQUIRED_FRONTMATTER - set(meta.keys())
        if missing:
            errors.append(f"{path.name}: missing required keys: {sorted(missing)}")

        if "id" in meta and not DEC_ID_RE.match(str(meta["id"])):
            errors.append(f"{path.name}: invalid id format: {meta['id']!r}")

        if "status" in meta and meta["status"] not in VALID_STATUSES:
            errors.append(
                f"{path.name}: invalid status {meta['status']!r}; "
                f"expected one of {sorted(VALID_STATUSES)}"
            )

    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print("frontmatter ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify MIDAS audit hash chain")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--chain-file",
        type=Path,
        default=DEFAULT_CHAIN,
        help=f"NDJSON audit chain file — the source of truth (default: {DEFAULT_CHAIN})",
    )
    parser.add_argument(
        "--check-frontmatter-only",
        action="store_true",
        help="only verify ADR YAML frontmatter (no DB required)",
    )
    args = parser.parse_args()

    if args.check_frontmatter_only:
        return verify_frontmatter()

    rc = 0
    checked = False

    # 1. The portable text chain is the source of truth — verify it first.
    if Path(args.chain_file).exists():
        ok, msg = chain.verify_file(args.chain_file)
        print(msg, file=sys.stderr if not ok else sys.stdout)
        rc |= 0 if ok else 1
        checked = True

    # 2. The SQLite projection, if present, must agree.
    if Path(args.db).exists():
        rc |= verify_chain(args.db)
        checked = True

    if not checked:
        print(
            f"nothing to verify: no chain file ({args.chain_file}) and no db ({args.db})",
            file=sys.stderr,
        )
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
