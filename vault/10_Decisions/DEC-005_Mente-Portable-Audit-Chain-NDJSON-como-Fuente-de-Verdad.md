---
id: DEC-005
title: 'Mente Portable: Audit Chain NDJSON como Fuente de Verdad'
type: decision
status: accepted
owner: Jordy Marin
created: '2026-05-20'
---

# DEC-005: Mente Portable: Audit Chain NDJSON como Fuente de Verdad

## Context

[[DEC-003]] estableció el esquema SQLite v1 con `audit_log` como cadena de hash
SHA-256 append-only (Merkle-like). Pero el `.db` de SQLite **no se versiona en git**
(está en `.gitignore`, junto a `.midas/` y `*.db`): es un binario local, frágil ante
corrupción, no diffeable y no portable. Si el archivo se pierde o se corrompe, se pierde
la memoria de auditoría completa del sistema —exactamente lo contrario de lo que MIDAS
necesita para ser "audit-grade".

La memoria de decisiones (la "mente" del sistema: ADRs, vetos, trips de gate, overrides)
debe **viajar con el repositorio**, ser legible por humanos, diffeable en PRs y
reconstruible desde texto plano.

## Decision

La cadena de hash SHA-256 vive en **`infra/audit/chain.ndjson`** como **fuente de verdad**:

- **Append-only NDJSON**: una línea JSON canónica (claves ordenadas, sin espacios) por
  evento. Cada evento lleva `seq`, `ts_utc`/`ts_ms`, `actor`, `event_type`, `event_key`,
  `payload`, `payload_hash`, `prev_hash` y `chain_hash`.
- **Encadenado**: `chain_hash = SHA-256(prev_hash || payload_hash)`; el primer evento usa
  `prev_hash` = 64 ceros. Idéntico invariante que `audit_log`.
- **Git-tracked**: texto plano, diffeable, viaja con el repo. SQLite (`audit_log` y el
  índice `decisions`) pasa a ser una **proyección** reconstruible.
- **Replay**: `init_db` reconstruye SQLite desde el chain (`chain.replay_to_db`). La
  dirección es siempre chain → SQLite, **nunca** al revés.

Implementación en `infra/audit/chain.py` (stdlib-only, sin dependencias nuevas):
`append_event`, `verify_file`, `project_event`, `replay_to_db`. `decision_log.py`
(`cmd_new`/`cmd_accept`) hace append al chain y luego proyecta; `verify_chain.py` valida
el archivo NDJSON además del `.db`.

## Consequences

### Positive

- **Portabilidad real**: se puede borrar el `.db` y recuperar toda la historia con un
  replay (`tests/test_chain_portability.py::test_mind_is_portable` lo prueba e2e).
- **Auditable en git**: cada decisión/veto queda en un diff revisable; tampering se
  detecta tanto en el archivo (`verify_file`) como en el `.db` (`verify_chain`).
- **Sin lock-in de SQLite**: el formato de verdad es texto JSON, no un binario.

### Negative / Trade-offs

- Doble escritura (chain + proyección SQLite) en cada evento; coste despreciable a este
  volumen (decisiones, no ticks).
- El archivo crece monótonamente; rotación/compactación queda para una fase futura.

### Risks

- **Edición manual del chain** rompería la cadena → mitigado: `verify_file` detecta
  cualquier alteración de `payload`, `prev_hash` o `chain_hash` y la CI lo corre.
- **Divergencia chain/SQLite** → mitigado: SQLite es desechable; el replay lo realinea.

## Alternatives Considered

- **Solo SQLite (status quo de [[DEC-003]])**: rechazado — binario no versionado, no
  portable, punto único de fallo para la memoria.
- **Commitear el `.db`**: rechazado — binarios en git no son diffeables, generan
  conflictos y violan la regla de `.gitignore` (`*.db`).
- **Postgres/servidor externo**: rechazado en Fase 1 por [[DEC-001]] (lockdown de stack,
  cero infra externa).

## References

- [[DEC-001]] — Stack Fase 1 Lockdown (sin deps fuera del stack).
- [[DEC-003]] — SQLite Schema v1 (origen de la cadena `audit_log`).
- `infra/audit/chain.py`, `infra/audit/chain.ndjson`
- `tests/test_chain_portability.py`
