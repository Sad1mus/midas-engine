"""Verify that all committed ADR notes have valid frontmatter.

This duplicates the pre-commit hook intentionally — pre-commit can be
bypassed; CI cannot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VAULT_DECISIONS = REPO_ROOT / "vault" / "10_Decisions"

REQUIRED_KEYS = {"id", "title", "type", "status", "owner", "created"}
VALID_STATUSES = {"proposed", "accepted", "superseded", "rejected"}


def _all_adrs() -> list[Path]:
    return sorted(VAULT_DECISIONS.glob("DEC-*.md"))


@pytest.mark.parametrize("path", _all_adrs(), ids=lambda p: p.name)
def test_adr_has_valid_frontmatter(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name} missing YAML frontmatter"
    parts = text.split("---\n", 2)
    assert len(parts) >= 3, f"{path.name} malformed frontmatter delimiters"
    meta = yaml.safe_load(parts[1]) or {}

    missing = REQUIRED_KEYS - set(meta.keys())
    assert not missing, f"{path.name} missing keys: {sorted(missing)}"

    assert meta["status"] in VALID_STATUSES, (
        f"{path.name} invalid status: {meta['status']!r}"
    )

    # ID must match filename
    expected_id = path.name.split("_", 1)[0]
    assert meta["id"] == expected_id, (
        f"{path.name} frontmatter id {meta['id']!r} != filename id {expected_id!r}"
    )


def test_at_least_four_foundational_decisions_exist() -> None:
    """Day 0 must ship DEC-001 through DEC-004."""
    ids = [p.name.split("_", 1)[0] for p in _all_adrs()]
    for expected in ("DEC-001", "DEC-002", "DEC-003", "DEC-004"):
        assert expected in ids, f"missing foundational decision: {expected}"
