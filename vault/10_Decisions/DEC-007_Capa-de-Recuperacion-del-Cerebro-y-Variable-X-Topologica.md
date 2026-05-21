---
id: DEC-007
title: Capa de Recuperacion del Cerebro y Variable X Topologica
type: decision
status: accepted
owner: Jordy Marin
created: '2026-05-20'
---

# DEC-007: Capa de Recuperacion del Cerebro y Variable 𝒳 Topologica

## Context

MIDAS acumula conocimiento en el vault (decisiones, regímenes, post-mortems,
ontología). Para que ese conocimiento sea *operativo* —no solo archivo— el sistema
necesita (a) **recuperar** las notas relevantes ante una situación y (b) **percibir
el régimen de mercado** de forma robusta y no lineal. Ambas piezas son Fase 1 y
deben respetar el lockdown de stack (DEC-001): sin servicios externos, sin deps
fuera de las ya listadas.

## Decision

Se introducen dos capas:

### 1. Recuperación (grafo + FTS5) — `core/knowledge/retrieval.py`

Dado `(query, instrumento?, régimen?)` se devuelven las notas más relevantes
combinando tres señales, **sin deps nuevas** (FTS5 viene en SQLite):

- **Texto (BM25)** sobre un índice FTS5 `notes_fts` del cuerpo/título/tags.
- **Estructura**: empujones por instrumento/régimen/tag.
- **Grafo**: las notas apuntadas por `[[wikilinks]]` de un buen match reciben un
  empujón. Un comando reindexa el vault (`python -m core.knowledge.retrieval reindex`).

### 2. Variable 𝒳 v1 — `sleeves/futures_tda/regime_x.py`

El operador 𝒳 detecta régimen vía **TDA**: embedding de Takens de los retornos →
Vietoris-Rips (`ripser`, **N_max = 10³ enforced**, DEC-001) → diagramas de
persistencia → features (μ, σ², entropía de persistencia, |H0|/|H1|) → divergencia
`D_topo` (bottleneck B0+B1, `persim`) vs una referencia → etiqueta a `regime_states`
(`classifier='tda_v1'`). v1 trabaja sobre OHLCV local; el order-flow L2 real es un
upgrade futuro que requerirá un DEC de datos.

Ambas se alimentan de la **capa de datos local** `data/loader.py` (CSV/parquet →
OHLCV normalizado; sin fuentes en vivo).

## Consequences

### Positive

- El cerebro deja de ser pasivo: recupera contexto relevante y etiqueta régimen.
- TDA captura cambios de estructura (no solo de varianza) de forma no paramétrica.
- Cero deps nuevas; todo reproducible y portable (regímenes a `regime_states`).

### Negative / Trade-offs

- BM25 es léxico (sin embeddings semánticos en Fase 1) — suficiente para el vault.
- `ripser` es O(coste) en el nº de puntos → de ahí el cap N_max = 10³.

### Risks

- **Falsos positivos de régimen** → mitigado: umbral calibrado (estable D_topo≤0.009
  vs shift≥0.044) y test de no-falsa-alarma sobre serie estacionaria.
- **Índice FTS desactualizado** → mitigado: el reindex es idempotente y barato.

## Alternatives Considered

- **Embeddings vectoriales (faiss/transformers)**: rechazado en Fase 1 — deps y peso
  fuera del lockdown (DEC-001); FTS5 cubre el caso.
- **Detección de régimen por HMM/vol clásica**: complementaria, pero 𝒳 aporta la
  señal topológica que es el diferenciador del proyecto (queda como `tda_v1`).

## References

- [[DEC-001]] — Stack Fase 1 Lockdown (sin deps nuevas; N_max=10³).
- [[DEC-003]] — SQLite Schema (`regime_states`).
- `core/knowledge/retrieval.py`, `sleeves/futures_tda/regime_x.py`, `data/loader.py`
- `tests/test_knowledge_retrieval.py`, `tests/test_regime_x.py`, `tests/test_data_loader.py`
