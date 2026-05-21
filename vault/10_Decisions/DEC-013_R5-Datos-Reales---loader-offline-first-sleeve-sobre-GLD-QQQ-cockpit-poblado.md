---
id: DEC-013
title: R5 Datos Reales - loader offline-first, sleeve sobre GLD QQQ, cockpit poblado
type: decision
status: accepted
owner: Jordy Marin
created: '2026-05-21'
---

# DEC-013: R5 — Datos reales (loader offline-first, sleeve sobre GLD/QQQ, cockpit poblado)

## Context

Tras [[DEC-012]] (aprobar yfinance), MIDAS vio por primera vez **datos de mercado
reales**. Este DEC documenta el resultado del Round 5: el loader, la integración con
el sleeve, la corrida sobre el histórico real y el veredicto honesto del pipeline
anti-overfitting — incluido un cockpit ahora poblado con equity/trades vivos.

## Decision

Se consolida la capa de datos reales y se deja constancia del primer veredicto
honesto sobre mercado real:

### Loader offline-first — `data/loaders/yfinance_loader.py`

`fetch_ohlcv(symbol, ...)` descarga de yfinance y **cachea** (offline-first: usa el
cache sin tocar la red). La ÚNICA función que toca la red es `_download`; los tests
la monkeypatchean y NUNCA llaman a Yahoo (usan el fixture `tests/fixtures/GLD_1d.csv`,
200 barras reales). Cache en **CSV** (no parquet: `pyarrow` no compila en Python 3.14,
ver [[DEC-012]]). Mapeo proxy→instrumento: **GLD→GC**, **QQQ→NQ**.

### Sleeve sobre datos reales — `scripts/run_real_data.py`

Reconstruye el `.db` desde el chain, corre el `SleeveRunner` sobre el histórico real
(sin cambios al hot path) y escribe trades/executions/equity_curve al `.db` +
eventos `trade`/`gate_trip` al **chain de PRODUCCIÓN**.

### Veredicto honesto (CPCV → DSR → PBO)

Sobre 1099 barras diarias reales (2022–2025):

| sleeve | DSR | PBO | veredicto | equity |
|---|---|---|---|---|
| `gld_gc_real` (oro) | 0.913 | 0.183 | **GO** | 50000 → 47396 |
| `qqq_nq_real` (NASDAQ) | 0.460 | 0.901 | **NO-GO** | 50000 → 45831 |

El DD-gate se disparó sobre datos reales (dd5/dd10). Ambos sleeves **perdieron**
dinero en su PnL — el baseline v1 (momentum/mean-rev condicionado por 𝒳) **no es un
edge**. El GO de GLD significa "la elección de lookback generaliza", NO "es rentable".

### Cockpit poblado

`scripts/cockpit.py` regenera el dashboard ya **NO vacío**: 2 sleeves (1 GO/1 NO-GO),
~50 trades, 49 regímenes, equity/drawdown/gate reales.

## Consequences

### Positive

- Primer ciclo end-to-end sobre datos reales, auditable en el chain y visible en Obsidian.
- El pipeline anti-overfitting funcionó honestamente: un NO-GO real, sin forzar nada.

### Negative / Trade-offs

- ETF ≠ futuro real; EOD diario (intradía limitado). El edge sigue **pendiente**.
- Trades/equity viven en el `.db` (proyección); el chain guarda los eventos de auditoría.

### Risks

- **Confundir GO con rentabilidad** → mitigado: GO = generaliza, no = gana; el PnL real
  fue negativo y queda documentado aquí.
- **Sobre-confiar en proxies** → mitigado: migrar a futuros reales será un DEC futuro.

## Alternatives Considered

- **Forzar un GO** ajustando umbrales o el sleeve: rechazado de plano — viola el espíritu
  del round (honestidad anti-overfitting). Un NO-GO es un resultado válido.
- **Buscar edge ya en R5:** fuera de alcance — el objetivo era ver datos reales, no ganar.

## References

- [[DEC-012]] — Adoptar yfinance (decisión humana de la fuente).
- [[DEC-008]] — CPCV/DSR/PBO (el pipeline que emitió el veredicto).
- [[DEC-011]] — Cockpit (poblado ahora con datos reales).
- `data/loaders/yfinance_loader.py`, `scripts/run_real_data.py`, `scripts/cockpit.py`,
  `tests/fixtures/GLD_1d.csv`.
