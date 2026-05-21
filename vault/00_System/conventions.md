---
id: SYS-002
title: Convenciones del Vault — Resumen Operativo
type: system
owner: Jordy Marin
created: 2026-05-20
tags: [system, conventions]
---

# Convenciones del Vault

Resumen operativo de [[10_Decisions/DEC-004_Vault-Conventions]].

## IDs

| Tipo | Patrón | Ejemplo |
|---|---|---|
| Decisión | `DEC-NNN` | `DEC-007` |
| Sleeve | `SLEEVE-NNN` | `SLEEVE-002` |
| Régimen | `REG-NNN` | `REG-014` |
| Post-mortem | `PM-YYYYMMDD-NNN` | `PM-20260815-002` |
| Daily | `DAILY-YYYY-MM-DD` | `DAILY-2026-08-15` |
| System | `SYS-NNN` | `SYS-001` |

## Frontmatter mínimo

```yaml
---
id: <ID>
title: <string>
type: decision | sleeve | regime | postmortem | daily | system
status: proposed | accepted | superseded | rejected   # solo decisions
owner: <name>
created: <YYYY-MM-DD>
tags: [array]
---
```

## Reglas inviolables

1. **No crear ADRs manualmente.** Usar `scripts/decision_log.py new`.
2. **No editar `id`, `created`, ni `content_hash` después de creado.**
3. **No mover archivos del Vault sin actualizar `decisions.vault_path` en SQLite.**
4. **No commit sin pasar `verify_chain.py --check-frontmatter-only`.**

## Tagging

- `foundational` — definiciones de arquitectura de día 0.
- `architecture` — decisiones que afectan a >1 capa.
- `sleeve` — relacionado con una estrategia específica.
- `risk` — relacionado con DD, vol targeting, gates.
- `compliance` — relacionado con regulación o prop firm rules.
- `audit` — relacionado con audit_log, hash chain.
- `temporary` — anotación de research que puede archivarse.
