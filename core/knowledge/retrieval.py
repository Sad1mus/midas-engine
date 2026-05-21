"""Capa de recuperación del cerebro de MIDAS.

Dado (query, instrumento?, régimen?) devuelve las notas del vault más relevantes
combinando tres señales, sin dependencias nuevas (FTS5 viene en SQLite):

  1. **Texto (BM25)** — índice FTS5 ``notes_fts`` sobre el cuerpo/título/tags.
  2. **Estructura** — coincidencia de carpeta, tag, instrumento o régimen.
  3. **Grafo** — enlaces ``[[wikilink]]`` entre notas (las vecinas de un buen match
     reciben un empujón).

El índice se materializa con ``reindex(vault_dir, db_path)`` (también vía CLI:
``python -m core.knowledge.retrieval reindex``). La búsqueda es determinista.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings  # noqa: E402

DEFAULT_INDEX_DB = REPO_ROOT / ".midas" / "knowledge.db"

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
TOKEN_RE = re.compile(r"[a-z0-9]+")

# Empujones (restan al score BM25; más negativo = mejor). Deterministas y suaves,
# nunca tan grandes como para invertir un match textual fuerte.
BOOST_INSTRUMENT = 0.5
BOOST_REGIME = 0.4
BOOST_TAG = 0.3
BOOST_GRAPH_NEIGHBOR = 0.2


@dataclass(frozen=True)
class RetrievedNote:
    path: str
    title: str
    score: float
    reasons: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────
# Parsing de notas
# ─────────────────────────────────────────────────────────────────────


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Devuelve (meta, body). Parser YAML mínimo (stack lockdown: evitamos pyyaml aquí
    para no acoplar; soporta los escalares y listas inline que usamos en el vault)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        val = raw.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            meta[key] = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
        elif val:
            meta[key] = val.strip("'\"")
    body = text[m.end() :]
    return meta, body


def _scan_vault(vault_dir: Path) -> list[dict[str, Any]]:
    """Recorre el vault y extrae metadatos + cuerpo + enlaces de cada nota .md."""
    notes: list[dict[str, Any]] = []
    for path in sorted(vault_dir.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        rel = path.relative_to(vault_dir).as_posix()
        folder = rel.split("/", 1)[0] if "/" in rel else ""
        tags = meta.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        links = sorted({m.split("|")[0].strip() for m in WIKILINK_RE.findall(text)})
        title = str(meta.get("title") or path.stem)
        notes.append(
            {
                "path": rel,
                "title": title,
                "folder": folder,
                "tags": [str(t) for t in tags],
                "instrument": meta.get("instrument"),
                "regime": meta.get("regime"),
                "links": links,
                "body": body,
                "id_key": str(meta.get("id") or path.stem),
            }
        )
    return notes


# ─────────────────────────────────────────────────────────────────────
# Índice
# ─────────────────────────────────────────────────────────────────────


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id          INTEGER PRIMARY KEY,
            path        TEXT NOT NULL UNIQUE,
            id_key      TEXT,
            title       TEXT NOT NULL,
            folder      TEXT,
            tags_json   TEXT NOT NULL DEFAULT '[]',
            instrument  TEXT,
            regime      TEXT,
            links_json  TEXT NOT NULL DEFAULT '[]',
            body        TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts "
        "USING fts5(path UNINDEXED, title, tags, body)"
    )


def reindex(vault_dir: Path | None = None, db_path: Path | None = None) -> int:
    """(Re)construye el índice FTS5 + tabla de notas desde el vault. Devuelve nº de notas."""
    vault = Path(vault_dir) if vault_dir is not None else settings.obsidian_vault_path
    db = Path(db_path) if db_path is not None else DEFAULT_INDEX_DB
    db.parent.mkdir(parents=True, exist_ok=True)

    notes = _scan_vault(vault)
    conn = sqlite3.connect(db)
    try:
        _ensure_schema(conn)
        conn.execute("DELETE FROM notes")
        conn.execute("DELETE FROM notes_fts")
        for n in notes:
            cur = conn.execute(
                """
                INSERT INTO notes
                    (path, id_key, title, folder, tags_json, instrument, regime, links_json, body)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    n["path"],
                    n["id_key"],
                    n["title"],
                    n["folder"],
                    json.dumps(n["tags"]),
                    n["instrument"],
                    n["regime"],
                    json.dumps(n["links"]),
                    n["body"],
                ),
            )
            conn.execute(
                "INSERT INTO notes_fts (rowid, path, title, tags, body) VALUES (?, ?, ?, ?, ?)",
                (cur.lastrowid, n["path"], n["title"], " ".join(n["tags"]), n["body"]),
            )
        conn.commit()
    finally:
        conn.close()
    return len(notes)


# ─────────────────────────────────────────────────────────────────────
# Búsqueda
# ─────────────────────────────────────────────────────────────────────


def _fts_query(query: str) -> str:
    """Convierte texto libre a una expresión FTS5 segura: `"t1" OR "t2" ...`."""
    terms = TOKEN_RE.findall(query.lower())
    return " OR ".join(f'"{t}"' for t in terms)


def retrieve(
    query: str,
    *,
    instrument: str | None = None,
    regime: str | None = None,
    db_path: Path | None = None,
    limit: int = 10,
) -> list[RetrievedNote]:
    """Devuelve las notas más relevantes (mejor primero) combinando BM25 + estructura + grafo."""
    db = Path(db_path) if db_path is not None else DEFAULT_INDEX_DB
    if not db.exists():
        return []
    fts_expr = _fts_query(query)
    if not fts_expr:
        return []

    conn = sqlite3.connect(db)
    try:
        try:
            rows = conn.execute(
                """
                SELECT f.path, n.title, n.folder, n.tags_json, n.instrument, n.regime,
                       n.links_json, n.id_key, bm25(notes_fts) AS score
                FROM notes_fts f
                JOIN notes n ON n.path = f.path
                WHERE notes_fts MATCH ?
                ORDER BY score
                """,
                (fts_expr,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []  # índice no construido todavía
        # resolver de wikilinks: id_key o stem del fichero -> path
        link_index: dict[str, str] = {}
        for id_key, npath in conn.execute("SELECT id_key, path FROM notes").fetchall():
            if id_key:
                link_index[id_key] = npath
            link_index[Path(npath).stem] = npath
    finally:
        conn.close()

    # Empujones de estructura
    base: dict[str, dict[str, Any]] = {}
    for path, title, _folder, tags_json, instr, reg, links_json, _id_key, score in rows:
        tags = json.loads(tags_json)
        reasons = ["bm25"]
        boost = 0.0
        if instrument and instr == instrument:
            boost += BOOST_INSTRUMENT
            reasons.append("instrument")
        if regime and reg == regime:
            boost += BOOST_REGIME
            reasons.append("regime")
        ql = query.lower()
        if any(t.lower() in ql for t in tags):
            boost += BOOST_TAG
            reasons.append("tag")
        base[path] = {
            "title": title,
            "score": float(score) - boost,
            "reasons": reasons,
            "links": json.loads(links_json),
        }

    # Empujón de grafo: una nota apuntada por un match recibe un empujón pequeño.
    for path, info in list(base.items()):
        for link in info["links"]:
            target = link_index.get(link)
            if target and target in base and target != path:
                base[target]["score"] -= BOOST_GRAPH_NEIGHBOR
                if "graph" not in base[target]["reasons"]:
                    base[target]["reasons"].append("graph")

    ranked = sorted(base.items(), key=lambda kv: (kv[1]["score"], kv[0]))
    return [
        RetrievedNote(path=p, title=i["title"], score=round(i["score"], 6), reasons=i["reasons"])
        for p, i in ranked[:limit]
    ]


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Recuperación sobre el vault (grafo + FTS5)")
    parser.add_argument("--db", type=Path, default=DEFAULT_INDEX_DB)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_idx = sub.add_parser("reindex", help="reindexar el vault a la tabla FTS")
    p_idx.add_argument("--vault", type=Path, default=settings.obsidian_vault_path)

    p_q = sub.add_parser("query", help="buscar notas relevantes")
    p_q.add_argument("text", type=str)
    p_q.add_argument("--instrument", default=None)
    p_q.add_argument("--regime", default=None)
    p_q.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()
    if args.cmd == "reindex":
        n = reindex(args.vault, args.db)
        print(f"reindexadas {n} notas → {args.db}")
        return 0
    if args.cmd == "query":
        hits = retrieve(
            args.text,
            instrument=args.instrument,
            regime=args.regime,
            db_path=args.db,
            limit=args.limit,
        )
        if not hits:
            print("(sin resultados)")
            return 0
        for h in hits:
            print(f"{h.score:+.4f}  {h.path}  [{', '.join(h.reasons)}]  — {h.title}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
