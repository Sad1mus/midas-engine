---
id: DEC-003
title: SQLite Schema v1 y Audit Hash Chain
type: decision
status: accepted
owner: Jordy Marin
created: 2026-05-20
tags: [persistence, audit, foundational]
---

# DEC-003: SQLite Schema v1 y Audit Hash Chain

## Context

Un fondo regulado debe poder demostrar la integridad de su historial
operativo a un auditor externo. La aproximación naive (logs en texto +
backups) no es suficiente: cualquier auditor sofisticado pedirá pruebas
criptográficas de no-modificación.

A su vez, el esquema necesita capturar desde día 0 todo lo que las
mejoras metodológicas (BMA correcto, CPCV/DSR/PBO, cost model, DD gates)
exigirán. Diseñar el schema de forma reactiva nos obliga a migrar
constantemente y a perder historia.

## Decision

Schema SQLite v1 (`infra/sqlite/migrations/001_initial.sql`) con las
siguientes tablas:

| Tabla | Propósito | Append-only |
|---|---|---|
| `schema_version` | Tracking de migraciones | No (single row) |
| `audit_log` | Hash chain SHA-256 inmutable | **Sí** |
| `decisions` | Índice de ADRs del Vault | Update sobre status |
| `sleeves` | Registry de estrategias | Update sobre status |
| `trades` | Trades propuestos / vetados / filled | Append + update status |
| `executions` | Fills reales (1..N por trade) | **Sí** |
| `equity_curve` | NAV por sleeve y agregado | **Sí** |
| `regime_states` | Output de regime classifier | **Sí** |
| `agent_decisions` | Inputs BMA de cada agente | **Sí** |
| `risk_gates_state` | Estado actual de DD gates | **Sí** |
| `cost_model_calibrations` | Almgren-Chriss params | **Sí** |
| `backtest_results` | CPCV outputs por sleeve candidato | **Sí** |

**Hash chain en `audit_log`:**

Cada fila contiene:

- `payload_json`: contenido serializado (JSON canonical, keys ordenadas).
- `payload_hash` = SHA-256(payload_json).
- `prev_hash` = `chain_hash` de la fila anterior (`0...0` para id=1).
- `chain_hash` = SHA-256(prev_hash || payload_hash).

Cualquier modificación retroactiva rompe la cadena de forma detectable
por `scripts/verify_chain.py`. Esto se ejecuta en CI nightly y antes de
cada cierre mensual de NAV.

**Eventos que se escriben a audit_log:**

- `decision` — creación, aceptación, supersedo de ADRs.
- `trade` — proposed → approved/vetoed.
- `execution` — fill recibido.
- `regime_change` — cambio de régimen detectado.
- `gate_trip` — DD gate activado (-5%, -10%, -15%, -20%).
- `override` — humano override de gate. **Doble firma obligatoria.**

**PRAGMAs por defecto:**

- `journal_mode = WAL` — concurrencia lectura-escritura.
- `synchronous = NORMAL` — balance durabilidad/perf.
- `foreign_keys = ON` — integridad referencial.
- `busy_timeout = 5000` — 5s antes de SQLITE_BUSY.

**Convenciones de tipos:**

- Timestamps: pareja `(ts_utc TEXT, ts_ms INTEGER)`. `ts_utc` ISO-8601
  UTC con sufijo `Z`. `ts_ms` unix milliseconds. Redundancia intencional
  para queries rápidas + auditoría legible.
- Booleans: `INTEGER 0/1` con CHECK constraints.
- Identificadores externos: ULID en TEXT (mejor que UUID por orden temporal).
- JSON: TEXT validado por aplicación (SQLite no enforced).

## Consequences

### Positive

- Auditoría criptográficamente verificable end-to-end.
- Schema cubre desde día 0 todas las necesidades de M1–M24.
- Migraciones forward-only versionadas (`NNN_*.sql`).
- WAL mode soporta el patrón típico (1 writer + N readers) sin lock.

### Negative / Trade-offs

- audit_log crece linealmente con la actividad. Estimación: ~50 MB/año
  con 10K eventos/día. Aceptable para Fase 1; rotación si excede 5 GB.
- Hash chain bloquea write batching: cada append es secuencial. Mitigado
  porque audit_log no está en hot path (trades se ejecutan vía Veto, el
  audit se escribe después con queue async).

### Risks

- **Corrupción de SQLite file**. Mitigado por:
  - Backup horario incremental del archivo (.db + .db-wal).
  - Replicación nightly a S3 / Backblaze B2.
  - `PRAGMA integrity_check` semanal automático.
- **Concurrent writer breaks the chain**. Mitigado por:
  - Único writer process garantizado por arquitectura (todos los appends
    pasan por `infra/audit/append.py`, single-flight queue).
  - Test de stress concurrencia en `tests/integration/test_audit_concurrency.py`.

## Alternatives Considered

- **Merkle tree en lugar de hash chain**: ofrece pruebas O(log n) de
  inclusión, pero coste de mantenimiento mayor. Hash chain basta para
  Fase 1; Merkle tree se evaluará si un LP exige Merkle proofs.
- **Append-only WAL log externo (Kafka, Redpanda)**: prematuro para escala
  Fase 1. Operacionalmente pesado.
- **Postgres desde día 1**: ya descartado en DEC-001.

## References

- `infra/sqlite/migrations/001_initial.sql` — esquema canónico.
- `scripts/init_db.py` — aplicador de migraciones.
- `scripts/verify_chain.py` — verificador de cadena.
- `scripts/decision_log.py` — ejemplo de uso del audit_log.
