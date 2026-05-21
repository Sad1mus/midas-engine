---
id: DEC-012
title: Adoptar yfinance para datos reales via ETF proxies (GLD QQQ)
type: decision
status: accepted
owner: Jordy Marin
created: '2026-05-21'
---

# DEC-012: Adoptar yfinance para datos reales vía ETF proxies (GLD, QQQ)

## Context

MIDAS ha corrido hasta ahora sobre datos **sintéticos** (R1–R4). Para salir del modo
sintético y que el cockpit muestre trades/equity vivos, hace falta una fuente de
datos de mercado **reales**. El stack está bajo lockdown ([[DEC-001]]): añadir una
dependencia de datos requiere una decisión **humana** explícita — este DEC.

Los socios eligieron (2026-05-20): **ETF proxies vía `yfinance`** — **GLD** (proxy de
oro / GC) y **QQQ** (proxy de NASDAQ / NQ). Gratis, sin API key, y son justamente los
mercados que operan.

## Decision

Se aprueba añadir **`yfinance`** a `pyproject.toml` como **única dependencia nueva**
del round. Ninguna otra entra sin otro DEC.

- **Proxies:** `GLD → GC` (oro), `QQQ → NQ` (NASDAQ), mapeados a `INSTRUMENT_SPECS`.
- **Offline-first:** el loader cachea OHLCV en `data/cache/` (carpeta **gitignored**).
  Si el cache existe, se usa **sin tocar la red**. La descarga live solo ocurre vía
  CLI (`python -m data.loaders.yfinance_loader`).
- **Formato de cache = CSV** (no parquet): `pyarrow`/`fastparquet` **no compilan en
  Python 3.14** en este entorno (sin wheel cp314; el sdist exige toolchains Arrow C++/
  Rust ausentes). Para respetar el lockdown (yfinance como única dep nueva), el cache y
  el fixture usan **CSV** — preserva idéntico el contrato offline-first y es además
  diffeable. Migrar a parquet será trivial cuando exista un engine instalable.
- **Tests sin red:** prohibido llamar a Yahoo en tests (serían flaky). Los tests usan
  un fixture CSV pequeño commiteado en `tests/fixtures/`.

## Consequences

### Positive

- Primeros datos de mercado reales en MIDAS; el cockpit deja de estar vacío.
- Gratis y reproducible (cache parquet); los proxies SON los instrumentos objetivo.

### Negative / Trade-offs

- Un ETF **no es el futuro real** (GLD ≠ contrato GC; QQQ ≠ NQ): difieren en horario,
  apalancamiento, microestructura y costos. Es un **proxy**, no la verdad de Track A.
- yfinance es no-oficial (scrapea Yahoo): puede romperse o limitar intradía; lo amplio
  y estable es **EOD/diario**.

### Risks

- **Yahoo caído / sin red** → mitigado: offline-first con cache parquet; los tests no
  dependen de la red (fixture commiteado).
- **Proxy engaña sobre el edge real** → mitigado: el pipeline anti-overfitting
  ([[DEC-008]]) sigue aplicando; un **NO-GO** sobre proxies es un resultado válido.
  Migrar a **futuros reales** (Databento/IBKR/Tradovate) será un **DEC futuro**.

## Alternatives Considered

- **Databento / IBKR / Tradovate (futuros reales):** rechazado *por ahora* — costo y/o
  credenciales; será un DEC futuro cuando haya edge que justifique pagar datos.
- **Seguir solo en sintético:** rechazado — el objetivo del round es ver datos reales.
- **CSV manual descargado a mano:** rechazado — no reproducible ni automatizable.

## References

- [[DEC-001]] — Stack Fase 1 Lockdown (por qué esto necesita decisión humana).
- [[DEC-008]] — CPCV/DSR/PBO (sigue aplicando sobre datos reales).
- `data/loaders/yfinance_loader.py` (loader offline-first), `data/cache/` (gitignored),
  `tests/fixtures/` (fixture CSV sin red).
