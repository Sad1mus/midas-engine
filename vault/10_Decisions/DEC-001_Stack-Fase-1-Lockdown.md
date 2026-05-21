---
id: DEC-001
title: Stack Fase 1 — Lockdown del Universo de Herramientas
type: decision
status: accepted
owner: Jordy Marin
created: 2026-05-20
tags: [architecture, stack, foundational]
---

# DEC-001: Stack Fase 1 — Lockdown del Universo de Herramientas

## Context

MIDAS Capital arranca desde cero. La tentación de elegir herramientas
"óptimas teóricas" (Neo4j, Ray, Rust nativo, computación cuántica)
mata la velocidad de validación de la tesis. Las hipótesis técnicas
H1–H4 ya están validadas formalmente con stack mínimo y mitigaciones
puntuales. Cualquier herramienta fuera de esa lista introduce coste
de oportunidad sin payoff demostrado.

## Decision

El stack Fase 1 es el **único** universo de herramientas permitido
hasta que se valide formalmente una migración mediante un DEC posterior
con criterios cuantitativos de superación.

**Lenguajes**

- Python 3.12 — orquestación, research, glue code.
- Rust + PyO3 — únicamente para el Veto actor y, eventualmente, hot loops
  cuya latencia esté validada como cuello de botella.

**Persistencia**

- SQLite (WAL mode) — todas las tablas de estado, audit_log, equity_curve,
  trades, executions, regime_states, agent_decisions, backtest_results.
- Migración a Postgres permitida sólo a partir de M12+ y bajo DEC nuevo
  con justificación de volumen o concurrencia que SQLite ya no soporte.

**Knowledge Base**

- Obsidian Vault (Markdown + YAML frontmatter) — **asíncrono estricto**.
  Nunca en hot path. Escritura desde Python en worker separado con queue
  bounded.

**Agentes**

- MCP via FastMCP. 11 agentes jerárquicos:
  - 5 núcleo crítico (Signal, Risk, Execution, Veto, Monitor)
  - 3 advisory opt-in (LLM-powered, fuera de hot path)
  - 3 ops (Audit, NAV, Compliance)

**LLMs**

- OpenRouter como gateway. Modelos: Claude (reasoning), DeepSeek (drafting,
  bulk). Nunca en el camino crítico de decisión de trade.

**TDA**

- ripser.py para Vietoris-Rips. `N_max = 10^3` enforced en código. Para
  `N > 10^3` se activa Sparse Rips automáticamente o se rechaza el cómputo.

**Workflow no-crítico**

- n8n — únicamente alertas, reportes, backups del Vault, recordatorios.
  **Nunca** ejecución de órdenes ni decisiones de trade.

**Brokers / Data**

- Tradovate (futuros) — Track A TopStep manual AI-assisted; Track B paper.
- IBKR (equity / options) — solo cuando se active el sleeve correspondiente.
- Databento Standard (CME L1+L2) — desde M3 al activar el primer backtest.

**Lo que está prohibido sin DEC nuevo**

- Neo4j, ArangoDB, cualquier graph DB.
- Ray, Dask, multiprocessing más allá de `ProcessPoolExecutor`.
- DuckDB, ClickHouse, Polars como backend principal (Polars como utility
  está permitido).
- Computación cuántica de cualquier proveedor.
- Cualquier framework de "AI agents" distinto a FastMCP.
- Cualquier broker distinto a Tradovate / IBKR.

## Consequences

### Positive

- Velocidad de iteración alta: stack manejable por un equipo pequeño.
- Auditabilidad: SQLite + Obsidian son trivialmente inspeccionables.
- Coste OPEX bajo (<$2K/mes en T1).
- Hipótesis H1–H4 ya cubren este stack formalmente.

### Negative / Trade-offs

- SQLite no escala horizontalmente. Hay techo en concurrencia de escritura
  (gestionado por single-writer queue en `infra/sqlite/`).
- Sin Neo4j, queries de grafo multi-hop sobre el Knowledge Base son lentas;
  se mitiga porque Obsidian no está en hot path.
- Sin Ray, no hay paralelización distribuida; aceptable para 11 agentes
  según H2.

### Risks

- **Tentación de over-engineering por miembros nuevos del equipo.** Mitigado
  por este DEC: cualquier PR que introduzca dep nueva requiere DEC.
- **SQLite locking bajo carga sostenida.** Mitigado por WAL mode + queue
  pattern. Si P99 escritura supera 10 ms en M6+ → DEC nuevo para Postgres.

## Alternatives Considered

- **DuckDB+Parquet hot path** (recomendación interna previa): rechazada
  por aumentar superficie de mantenimiento sin SLA documentado. SQLite
  basta para volúmenes de Fase 1.
- **PostgreSQL desde día 1**: rechazada por OPEX y complejidad operativa
  (backups, replicación) prematura.
- **Neo4j para Knowledge Base**: rechazada por complejidad. Obsidian
  asíncrono cubre el caso de uso real (auditoría y razonamiento humano),
  no querying multi-hop en producción.
- **Ray para agentes**: rechazada por overhead de serialización (Pickle,
  Plasma, gRPC). H2 validó que Python+FastMCP basta para 11 agentes.

## References

- `vault/10_Decisions/DEC-002_Monorepo-Layout.md`
- `vault/10_Decisions/DEC-003_SQLite-Schema-v1.md`
- `vault/10_Decisions/DEC-004_Vault-Conventions.md`
- Documento de validación formal del stack (H1–H4).
- López de Prado, *Advances in Financial Machine Learning* (2018).
