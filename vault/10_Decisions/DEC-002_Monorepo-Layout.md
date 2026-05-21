---
id: DEC-002
title: Monorepo Layout y Separación de Responsabilidades
type: decision
status: accepted
owner: Jordy Marin
created: 2026-05-20
tags: [architecture, repo, foundational]
---

# DEC-002: Monorepo Layout y Separación de Responsabilidades

## Context

Un quant fund moderno orquesta múltiples sleeves, agentes, pipelines de
research, y operaciones de fondo. Polirrepos fragmentan el contexto y
disparan el coste de coordinación entre componentes que comparten
schemas (SQLite), invariantes (DD-first gates) y deployment.

## Decision

Monorepo único con la siguiente jerarquía. Cada subdirectorio tiene
**responsabilidad única** y bounded interface con el resto.

```
midas-capital/
├── core/                  # Hot path determinista. Veto, execution, risk, state.
├── agents/                # 11 agentes MCP (5 core + 3 advisory + 3 ops).
├── sleeves/               # Estrategias. Una carpeta por sleeve, autocontenida.
│   ├── futures_tda/
│   ├── equity_statarb/
│   ├── crypto_basis/
│   └── options_volsell/
├── research/              # Backtesting, CPCV, DSR, PBO, cost models, notebooks.
├── data/                  # Ingestión por broker (tradovate, databento, ibkr).
├── infra/                 # SQLite schemas, obsidian sync, audit chain.
├── scripts/               # CLI tooling (init_db, decision_log, verify_chain).
├── vault/                 # Obsidian Vault. Knowledge base canónico.
└── tests/                 # Suite (unit + integration).
```

**Reglas de dependencia (DAG enforced por mypy + tests):**

- `core` no importa de `agents`, `sleeves`, `research`, `data`.
- `agents` no importa de `sleeves`.
- `sleeves` importa de `core`, `data`, `research` (utilidades, no notebooks).
- `research` no se importa desde producción excepto utilidades cuantitativas
  (CPCV, DSR, PBO, cost models).
- `infra` lo importa todo el resto. `infra` no importa nada de negocio.
- `scripts` puede importar de cualquier sitio pero no se importa desde nada.

**Sleeves son autocontenidos.** Cada `sleeves/<name>/` tiene su propia
estrategia, su signal generator, su sizing logic, sus tests. Comparten
solo:

- Contract de salida: `Signal(sleeve_id, ts, instrument, side, size_target, ...)`.
- DD gates y vol targeting del portfolio manager (en `core/risk/`).
- Schema de persistencia (`infra/sqlite/`).

**Un sleeve nuevo no puede importar lógica de otro sleeve.** Si necesita
algo compartido, va a `core/` o `research/` mediante PR + DEC si introduce
acoplamiento estructural.

## Consequences

### Positive

- Atomic commits cruzan capas (schema + código + test + decisión) sin
  coordinar repos.
- Sleeves pueden retirarse o pausarse sin afectar al resto.
- CI/CD único.
- Onboarding rápido para nuevos miembros.

### Negative / Trade-offs

- Repositorio crece linealmente con número de sleeves. Aceptable hasta
  ~15 sleeves; después se evaluará split.
- Build time crece. Mitigado por pytest selection y módulos opcionales.

### Risks

- **Acoplamiento implícito entre sleeves.** Mitigado por DAG de dependencias
  enforced por test estructural (`tests/test_imports.py` planeado).
- **Repo bloat con notebooks.** Notebooks viven en `research/notebooks/`
  con `.gitignore` agresivo de outputs.

## Alternatives Considered

- **Polirrepo (1 repo por sleeve)**: rechazado por coste de coordinación
  y duplicación de schema definitions.
- **Mono-package único `midas/`**: rechazado por mezclar capas con
  responsabilidades distintas.

## References

- `vault/10_Decisions/DEC-001_Stack-Fase-1-Lockdown.md`
- `vault/10_Decisions/DEC-003_SQLite-Schema-v1.md`
