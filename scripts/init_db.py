"""Initialize the MIDAS Capital SQLite database.

Applies migrations from infra/sqlite/migrations/ in order, idempotently.
Safe to run repeatedly; only new migrations are applied.

Usage:
    python scripts/init_db.py                     # default path .midas/midas.db
    python scripts/init_db.py --db /tmp/test.db   # custom path
    python scripts/init_db.py --reset             # WIPE and reinit (dev only)
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.audit import chain  # noqa: E402

MIGRATIONS_DIR = REPO_ROOT / "infra" / "sqlite" / "migrations"
DEFAULT_DB = REPO_ROOT / ".midas" / "midas.db"
DEFAULT_CHAIN = chain.DEFAULT_CHAIN

MIGRATION_FILENAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def _git_commit() -> str | None:
    """Return current git HEAD short SHA, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=REPO_ROOT,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


def _discover_migrations() -> list[tuple[int, Path]]:
    """Return list of (version, path) for all migrations, sorted ascending."""
    migrations: list[tuple[int, Path]] = []
    for path in MIGRATIONS_DIR.glob("*.sql"):
        m = MIGRATION_FILENAME_RE.match(path.name)
        if not m:
            print(
                f"warning: skipping {path.name} (does not match NNN_name.sql pattern)",
                file=sys.stderr,
            )
            continue
        migrations.append((int(m.group(1)), path))
    migrations.sort(key=lambda t: t[0])
    return migrations


def _current_version(conn: sqlite3.Connection) -> int:
    """Return current schema_version or 0 if table does not exist yet."""
    try:
        row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _apply_migration(conn: sqlite3.Connection, version: int, path: Path) -> None:
    """Apply a single migration file and record it in schema_version."""
    sql = path.read_text(encoding="utf-8")
    now = dt.datetime.now(dt.UTC)
    ts_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    ts_ms = int(now.timestamp() * 1000)
    commit = _git_commit()

    print(f"  applying migration {version:03d} ({path.name}) ...", end=" ", flush=True)

    try:
        conn.executescript(sql)
        # Record version. Some migrations may already create schema_version themselves;
        # we INSERT OR REPLACE to be idempotent.
        conn.execute(
            """
            INSERT INTO schema_version (id, version, applied_at_utc, applied_at_ms, git_commit)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                version = excluded.version,
                applied_at_utc = excluded.applied_at_utc,
                applied_at_ms = excluded.applied_at_ms,
                git_commit = excluded.git_commit
            """,
            (version, ts_iso, ts_ms, commit),
        )
        conn.commit()
        print("ok")
    except sqlite3.Error as e:
        conn.rollback()
        print("FAIL")
        raise RuntimeError(f"migration {version:03d} failed: {e}") from e


def init_db(db_path: Path, *, reset: bool = False, chain_path: Path | None = None) -> int:
    """Initialize the database at db_path. Returns the final schema_version.

    If ``chain_path`` is given and the chain file exists, the audit_log +
    decisions projections are rebuilt from it via :func:`chain.replay_to_db`
    after migrations run — the text chain is the source of truth. When
    ``chain_path`` is None (the default, e.g. in unit tests) no replay happens.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if reset and db_path.exists():
        print(f"reset: removing existing {db_path}")
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        current = _current_version(conn)
        migrations = _discover_migrations()
        pending = [(v, p) for v, p in migrations if v > current]

        print(f"db: {db_path}")
        print(f"current schema_version: {current}")
        print(f"available migrations: {len(migrations)}; pending: {len(pending)}")

        if pending:
            for version, path in pending:
                _apply_migration(conn, version, path)
            final = _current_version(conn)
            print(f"\nfinal schema_version: {final}")
        else:
            print("already up to date.")
            final = current

        if chain_path is not None and Path(chain_path).exists():
            n = chain.replay_to_db(conn, chain_path)
            print(f"replayed {n} event(s) from chain: {chain_path}")

        return final
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize MIDAS SQLite database")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"database path (default: {DEFAULT_DB.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="wipe existing db before applying migrations (dev only)",
    )
    parser.add_argument(
        "--chain-file",
        type=Path,
        default=DEFAULT_CHAIN,
        help=f"NDJSON audit chain to replay as source of truth (default: {DEFAULT_CHAIN})",
    )
    parser.add_argument(
        "--no-replay",
        action="store_true",
        help="skip replaying the audit chain into SQLite",
    )
    args = parser.parse_args()

    try:
        init_db(args.db, reset=args.reset, chain_path=None if args.no_replay else args.chain_file)
        return 0
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
