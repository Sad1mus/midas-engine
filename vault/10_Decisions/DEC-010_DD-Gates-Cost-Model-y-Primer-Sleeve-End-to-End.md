---
id: DEC-010
title: DD-Gates Cost Model y Primer Sleeve End-to-End
type: decision
status: accepted
owner: Jordy Marin
created: '2026-05-21'
---

# DEC-010: DD-Gates, Cost Model y Primer Sleeve End-to-End

## Context

Tras la mente portable ([[DEC-005]]), Magister ([[DEC-006]]), el cerebro ([[DEC-007]])
y el pipeline anti-overfitting ([[DEC-008]]), faltaba cerrar el **primer slice
vertical** que conecta todo: del dato al trade, con la supervivencia como principio
rector. Este DEC documenta tres piezas que se construyeron juntas y ya están en uso.

## Decision

### 1. Motor de DD-gates (drawdown-first) — `core/risk/gates.py`

Máquina de estados monótona y **solo restrictiva**:

    normal → dd5 → dd10 → dd15 → halt
    sizing_mult:  1.0   0.5    0.25   0.125   0.0   (umbrales 5/10/15/20%)

Lee el drawdown de `equity_curve` (pico vs equity), escribe el estado a
`risk_gates_state` y emite `gate_trip` al hash chain al cruzar un umbral. El Risk
Manager aplica `sizing_mult` en el paso 4 (después de reglas duras y ontología):
el tamaño final **solo se reduce, nunca aumenta** (DEC-001). `halt` ⇒ size 0.

### 2. Cost model Almgren-Chriss v1 — `research/cost_model/`

Calibra por (instrumento, hora) desde OHLCV/volumen: spread proxy + impacto temporal
η y permanente γ (∝ vol_dólar/liquidez), persistido en `cost_model_calibrations`.
`estimate_cost(order)` = ½·spread·tick_value·X + γ·X² + η·X²/horizonte — monótono
creciente en el tamaño. Alimenta la simulación de fills del sleeve.

### 3. Primer sleeve end-to-end (paper · Track B) — `sleeves/futures_tda/runner.py`

Corre el LOOP COMPLETO sobre datos locales: loader → Variable 𝒳 (régimen) → señal
condicionada (estable→momentum, shift→mean-reversion) → `TradeSignal` → **Risk
Manager** (reglas + ontología + DD-gates) → fills con el cost model →
`trades`/`executions`/`equity_curve`. **Invariante: ninguna orden evita el Veto** —
solo se ejecuta con APPROVE; un REJECT queda como trade `vetoed` sin ejecución.

Su validación ([[DEC-008]]) vía CPCV/DSR/PBO (`sleeves/futures_tda/validate.py`)
emite **GO/NO-GO** (GO ⟺ DSR>0 y PBO<0.5). El sleeve v1 da NO-GO sobre sintético
(PBO alto): el pipeline lo frena, como debe.

## Consequences

### Positive

- Slice vertical completo y auditable (cada trade/gate anclado al chain).
- La supervivencia es estructural: los gates reducen exposición antes de maximizar
  retorno; el Veto es infranqueable.
- Costos realistas en el backtest (impacto cuadrático), no fills idealizados.

### Negative / Trade-offs

- Señal baseline v1 simple (momentum/mean-rev); el alfa real es iteración futura.
- Cost model calibrado de OHLCV (sin datos de ejecución reales aún → DEC de datos).

### Risks

- **Sleeve overfit promovido por error** → mitigado: gate GO/NO-GO con PBO; v1 da NO-GO.
- **Gate mal calibrado** → mitigado: umbrales explícitos y `gate_trip` auditable;
  los gates solo reducen, nunca aumentan (invariante con `apply_sizing`).

## Alternatives Considered

- **Position sizing fijo sin gates:** rechazado — viola el principio drawdown-first.
- **Fills sin costo de impacto:** rechazado — sobreestima el desempeño; el cost model
  cuadrático es justo lo que evita esa trampa.

## References

- [[DEC-001]] — Stack Fase 1 Lockdown (sin deps nuevas; gates solo reducen).
- [[DEC-003]] — SQLite Schema (`equity_curve`, `risk_gates_state`, `trades`,
  `executions`, `cost_model_calibrations`, `backtest_results`).
- [[DEC-005]] — Mente Portable (eventos `gate_trip`/`trade` al chain).
- [[DEC-007]] — Variable 𝒳 (régimen del sleeve). [[DEC-008]] — CPCV/DSR/PBO (validación).
- `core/risk/gates.py`, `research/cost_model/`, `sleeves/futures_tda/runner.py`,
  `sleeves/futures_tda/validate.py`
