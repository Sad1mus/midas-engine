---
id: DEC-004
title: Convenciones del Obsidian Vault y Write-Back Asíncrono
type: decision
status: accepted
owner: Jordy Marin
created: 2026-05-20
tags: [knowledge-base, obsidian, foundational]
---

# DEC-004: Convenciones del Obsidian Vault y Write-Back Asíncrono

## Context

Obsidian es la corteza semántica del sistema: donde los humanos razonan,
los post-mortems se escriben, las ontologías se mantienen. Pero el Vault
**no** puede estar en el hot path de ejecución de trade. Esto fue
explícitamente validado en la hipótesis H1 del documento formal: escritura
síncrona a YAML/Markdown es inestable bajo carga.

A su vez, sin convenciones estrictas, el Vault degenera en notas inconexas
que pierden valor.

## Decision

### Estructura del Vault

```
vault/
├── 00_System/         # Convenciones, índice, glosario, este DEC.
├── 10_Decisions/      # ADRs (DEC-NNN_*.md).
├── 20_Sleeves/        # Una nota por sleeve, con su lifecycle, métricas.
├── 30_Regimes/        # Catálogo de regímenes detectados, características.
├── 40_Postmortems/    # Post-mortem de cada gate trip y de eventos críticos.
├── 50_Daily/          # Daily journal automatizado (M-F) por agente Audit.
└── 90_Archive/        # Notas obsoletas que se preservan por auditoría.
```

### Frontmatter obligatorio

Toda nota en el Vault debe empezar con YAML frontmatter:

```yaml
---
id: <DEC-001 | SLEEVE-001 | REG-001 | PM-001 | DAILY-YYYY-MM-DD>
title: <string>
type: <decision | sleeve | regime | postmortem | daily | system>
status: <proposed | accepted | superseded | rejected>  # solo decisions
owner: <name>
created: <YYYY-MM-DD>
tags: [array]
---
```

Verificado por `scripts/verify_chain.py --check-frontmatter-only` en
pre-commit.

### Write-back asíncrono desde Python

**Regla inviolable:** Python escribe a Obsidian **solo** desde un worker
asíncrono dedicado en `infra/obsidian_sync/`. Nunca desde:

- Hot path de Veto.
- Hot path de Execution.
- Hot path de Risk gates.
- Cualquier función dentro de `core/`.

**Mecanismo:**

1. Productores empujan eventos a una `asyncio.Queue` con `maxsize=1000`.
2. Si la queue está llena, el productor **descarta** el evento (no bloquea).
   Audit log es la fuente de verdad; Obsidian es proyección.
3. Worker consume y escribe a `vault/...` con timestamp y hash en frontmatter.
4. Watchfiles detecta cambios humanos en el Vault y los proyecta a
   `audit_log` con `actor = 'human:<name>'`.

### Hash en frontmatter

Cuando Python crea o modifica una nota:

- `content_hash`: SHA-256 del body (sin frontmatter).
- `audit_id`: id del audit_log entry correspondiente.

Esto permite cross-reference: dado una nota, encontrar todos los eventos
que la afectaron, y viceversa.

### Sleeve notes (20_Sleeves/)

Una nota por sleeve. Estructura:

```markdown
---
id: SLEEVE-001
title: Futures TDA v1
type: sleeve
owner: <name>
status: research | paper | live | paused | retired
created: YYYY-MM-DD
tags: [futures, tda]
---

## Hypothesis
## Universe
## Signal generation
## Risk parameters
## Performance metrics (auto-actualizadas por Python)
## Lifecycle log (DSR, PBO, regime changes)
```

### Postmortem notes (40_Postmortems/)

**Automático** en cualquier gate trip (-5%, -10%, -15%, halt). El agente
Audit crea la plantilla; el humano completa la sección "Lessons" antes
del próximo trading day.

```markdown
---
id: PM-YYYYMMDD-001
title: Gate -10% trip on SLEEVE-001
type: postmortem
owner: <auto-filled>
created: YYYY-MM-DD
trigger_audit_id: <int>
sleeve_id: <string>
gate_level: dd5 | dd10 | dd15 | halt
---

## What happened (auto)
## Diagnosis (human)
## Action items (human)
## Recovery plan (human)
```

## Consequences

### Positive

- Vault es legible y útil para humanos sin disciplina externa.
- Hot path nunca bloqueado por I/O de archivos.
- Cross-reference Vault ↔ audit_log mantenido por hashing.

### Negative / Trade-offs

- Convención estricta exige discipline; mitigado por verify_chain en pre-commit.
- Eventos descartados bajo presión de queue. Aceptable porque audit_log
  es source of truth.

### Risks

- **Vault drift**: notas creadas manualmente sin frontmatter. Mitigado
  por pre-commit hook que rechaza commits.
- **Race conditions human-edit vs python-write**: mitigado por:
  - Python solo escribe a directorios `*_auto/` dentro de cada sección.
  - Humano edita directamente cualquier archivo; cambios se proyectan a
    audit_log con `actor=human:<name>`.

## Alternatives Considered

- **Vault como source of truth en lugar de proyección**: rechazado.
  Filesystem watch es frágil para concurrencia; SQLite es robusto.
- **JSON en lugar de YAML frontmatter**: rechazado. Obsidian usa YAML
  nativamente y permite Dataview queries.

## References

- `vault/00_System/conventions.md` — versión operacional de este DEC.
- `infra/obsidian_sync/` — implementación del worker (pendiente, M2).
- Documento de validación formal H1.
