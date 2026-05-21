---
id: DEC-008
title: Pipeline Anti-Overfitting CPCV DSR PBO
type: decision
status: accepted
owner: Jordy Marin
created: '2026-05-20'
---

# DEC-008: Pipeline Anti-Overfitting CPCV DSR PBO

## Context

El mayor riesgo de una mesa cuantitativa no es el mercado: es **engañarse a uno
mismo**. Probar miles de configuraciones y quedarse con la mejor in-sample produce
backtests espectaculares que mueren en producción (overfitting). Antes de que una
estrategia (sleeve) pase de research a paper/live, MIDAS debe medir cuánto de su
desempeño es señal y cuánto es azar seleccionado.

## Decision

Se adopta un pipeline de validación estilo **López de Prado**, en `research/`:

- **`research/cpcv/`** — Combinatorial Purged Cross-Validation: N grupos, todas las
  combinaciones de k como test (C(N,k) *paths*), con **purge** (elimina train cuyo
  horizonte solapa el test) y **embargo** (margen posterior). Descontamina la fuga
  temporal.
- **`research/deflated_sharpe/`** — Deflated Sharpe Ratio: corrige el Sharpe por el
  nº de pruebas (SR0 = máximo esperado bajo la nula), la longitud de la serie y la
  no-normalidad (asimetría/curtosis). DSR ∈ [0,1].
- **`research/pbo/`** — Probability of Backtest Overfitting por CSCV: fracción de
  combinaciones donde el ganador in-sample queda bajo la mediana out-of-sample.
- **`research/backtest.py::run_validation`** — orquesta todo sobre una matriz de
  performance (T×C), elige la config candidata, calcula PBO/DSR/Sharpe(mean,std)/
  max_dd y **escribe a `backtest_results`**, anclando un evento `backtest` al hash
  chain (fuente de verdad, [[DEC-005]]).

**Regla de gobierno:** un sleeve con **PBO alto** (la marca de overfit) no asciende
de track. La promoción de estrategias se apoya en esta evidencia, no en el Sharpe
desnudo.

## Consequences

### Positive

- Criterio objetivo y reproducible para promover/descartar estrategias.
- PBO/DSR persistidos y auditables (`backtest_results` + chain).
- Sin deps nuevas más allá del stack científico ya en pyproject (DEC-001).

### Negative / Trade-offs

- CSCV es combinatorio: coste ~ C(S, S/2); se acota con `n_partitions` moderado.
- v1 valida sobre una matriz de retornos; el motor de estrategias que la genera es
  iteración posterior.

### Risks

- **Mala parametrización de purge/embargo** subestimaría el overfit → mitigado:
  son parámetros explícitos, persistidos en `backtest_results` (embargo_pct/purge_pct).
- **Pocos paths** daría estimaciones ruidosas → mitigado: `n_paths ≥ 10` (CHECK del
  esquema), C(6,2)=15 por defecto.

## Alternatives Considered

- **Walk-forward / k-fold simple**: rechazado — sin purge/embargo filtra información
  y sobreestima el desempeño en series temporales.
- **Solo Sharpe anualizado**: rechazado — no corrige el sesgo de selección; es justo
  la trampa que este pipeline existe para evitar.

## References

- [[DEC-001]] — Stack Fase 1 Lockdown (deps del stack científico).
- [[DEC-003]] — SQLite Schema (`backtest_results`).
- [[DEC-005]] — Mente Portable (el evento `backtest` se ancla al chain).
- `research/cpcv/`, `research/deflated_sharpe/`, `research/pbo/`, `research/backtest.py`
- `tests/test_research_validation.py`
