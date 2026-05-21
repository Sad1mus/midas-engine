"""Tests de la capa de recuperación del cerebro (grafo + FTS5)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.knowledge import retrieval  # noqa: E402


def _write_note(
    vault: Path, rel: str, *, title: str, body: str, tags: str = "", extra: str = ""
) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    front = f"---\ntitle: {title}\n"
    if tags:
        front += f"tags: [{tags}]\n"
    if extra:
        front += extra + "\n"
    front += "---\n\n"
    path.write_text(front + body, encoding="utf-8")


def test_bm25_ranks_gold_note_first(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    db = tmp_path / "knowledge.db"

    _write_note(
        vault,
        "30_Regimes/gold_vol.md",
        title="Oro y volatilidad",
        body=(
            "Gold tends to spike in volatility during macro stress. "
            "Rising gold volatility often precedes risk-off regimes."
        ),
    )
    _write_note(
        vault,
        "30_Regimes/nasdaq.md",
        title="Nasdaq momentum",
        body="The nasdaq tech index rallied on strong earnings and momentum.",
    )

    n = retrieval.reindex(vault, db)
    assert n == 2

    hits = retrieval.retrieve("gold volatility", db_path=db)
    assert hits, "se esperaba al menos un resultado"
    assert hits[0].path == "30_Regimes/gold_vol.md"
    assert "bm25" in hits[0].reasons
    # la nota nasdaq no comparte términos con la query → no aparece
    assert all(h.path != "30_Regimes/nasdaq.md" for h in hits)


def test_structure_boost_instrument(tmp_path: Path) -> None:
    """Con dos notas que empatan en texto, la del instrumento pedido gana."""
    vault = tmp_path / "vault"
    db = tmp_path / "knowledge.db"

    _write_note(
        vault,
        "20_Sleeves/a.md",
        title="Setup A",
        body="breakout setup with strong momentum and volume.",
        extra="instrument: ES",
    )
    _write_note(
        vault,
        "20_Sleeves/b.md",
        title="Setup B",
        body="breakout setup with strong momentum and volume.",
        extra="instrument: NQ",
    )
    retrieval.reindex(vault, db)

    hits = retrieval.retrieve("breakout momentum", instrument="NQ", db_path=db)
    assert hits[0].path == "20_Sleeves/b.md"
    assert "instrument" in hits[0].reasons


def test_graph_neighbor_boost(tmp_path: Path) -> None:
    """Una nota apuntada por wikilink desde un match recibe empujón de grafo."""
    vault = tmp_path / "vault"
    db = tmp_path / "knowledge.db"

    _write_note(
        vault,
        "10_Decisions/DEC-100_main.md",
        title="Main",
        body="topic alpha discussion linking to context.",
        extra="id: DEC-100",
    )
    # nota vecina enlazada desde la principal; comparte el término 'alpha' débilmente
    _write_note(
        vault,
        "10_Decisions/DEC-101_ctx.md",
        title="Context",
        body="alpha context note. See [[DEC-100]] for the main rationale.",
        extra="id: DEC-101",
    )
    retrieval.reindex(vault, db)

    hits = retrieval.retrieve("alpha", db_path=db)
    paths = {h.path for h in hits}
    assert "10_Decisions/DEC-100_main.md" in paths


def test_empty_or_missing_index_is_safe(tmp_path: Path) -> None:
    db = tmp_path / "nope.db"
    assert retrieval.retrieve("anything", db_path=db) == []
    # query vacía sobre índice existente
    vault = tmp_path / "vault"
    _write_note(vault, "x.md", title="X", body="hello world")
    retrieval.reindex(vault, db)
    assert retrieval.retrieve("   ", db_path=db) == []
