---
id: DEC-006
title: 'Magister: Escritor Unico de Reglas Ontologicas Aditivas'
type: decision
status: accepted
owner: Jordy Marin
created: '2026-05-20'
---

# DEC-006: Magister: Escritor Unico de Reglas Ontologicas Aditivas

## Context

El humano necesita poder inyectar conocimiento de riesgo en MIDAS sin tocar
código ni el motor del Veto: "congela NQ ±30min de FOMC", "reduce a la mitad el
size de GC en la apertura". Pero abrir un canal de escritura al núcleo de riesgo
es peligroso —es exactamente donde una regla mal puesta podría *aflojar* el Veto
o saltarse un límite duro.

El núcleo de riesgo (`core/risk`) es, por constitución, determinista y solo
restrictivo: la IA propone, el riesgo dispone. Cualquier mecanismo de inyección
humana debe preservar ese invariante de forma estructural, no por convención.

## Decision

Se introduce **Magister**, el **escritor único** de reglas ontológicas.

### Modelo de reglas (tabla `ontology_rules`, schema v2)

Reglas **aditivas y solo restrictivas**: `action ∈ {veto, size_down}`. Cada regla
tiene tipo, instrumento (o todos), ventana (`window_spec`), params y `active 0/1`.
Ver migración `002_ontology_rules.sql`.

### Magister como cuello de botella de escritura

`agents/advisory/magister.py::inject_rule` es el ÚNICO punto que crea reglas.
En un acto atómico:

1. **Append al hash chain** `infra/audit/chain.ndjson` (fuente de verdad, ver
   [[DEC-005]]) con `event_type = ontology_rule`.
2. **Proyección** a `ontology_rules` (vía `chain.project_event`, así la regla es
   portable: replay la reconstruye).
3. **Proyección** de una nota legible a `vault/20_Ontology/<id>.md`.

El skill `/magister` (`.claude/skills/magister/`) traduce la instrucción en
lenguaje natural a los campos estructurados e invoca a este helper.

### Aplicación por el Veto

`core/risk/ontology.py` lee las reglas activas; `manager.evaluate` las aplica en
el **paso 3, DESPUÉS** de las reglas duras y del kill switch. Una regla `veto`
rechaza; una `size_down` recorta el tamaño. Como se aplican sobre una señal que
*ya pasó* las reglas duras, son incapaces por construcción de aprobar algo que el
Veto rechazaría.

### Invariante (triple defensa)

"Una regla de Magister nunca aprueba, solo restringe" se garantiza en tres capas:
`inject_rule` lanza `ValueError` ante una acción no restrictiva; el `__post_init__`
de `OntologyRule` la rechaza; y el `CHECK (action IN ('veto','size_down'))` de
SQLite la bloquea en disco.

## Consequences

### Positive

- El humano gobierna el riesgo en lenguaje natural, con trazabilidad total
  (cada regla anclada al chain y diffeable en git).
- El Veto se endurece sin riesgo de aflojarse; el invariante es estructural.
- Reglas portables: borrar el `.db` y `init_db` las recupera por replay.

### Negative / Trade-offs

- Magister es un cuello de botella deliberado: no hay atajos de escritura.
- El matching de ventanas de evento depende de un `event_ts` explícito (Fase 1
  sin calendario económico integrado).

### Risks

- **Regla demasiado amplia** (p. ej. `instrument=null` + sin ventana) podría
  vetar de más → mitigado: las reglas son visibles, auditables y desactivables
  (`active=0` vía evento `deactivate`); nunca silenciosas.
- **Edición manual de `ontology_rules`** saltándose a Magister → mitigado: la
  verdad es el chain; un replay realinea y `verify_chain` delata divergencias.

## Alternatives Considered

- **Reglas hardcodeadas en `core/risk/rules.py`**: rechazado — requiere deploy de
  código para cada ajuste y mezcla gobernanza humana con lógica del motor.
- **Escritura directa del LLM a `ontology_rules`**: rechazado — sin un cuello de
  botella que valide el invariante, una alucinación podría intentar aprobar.
- **Reglas bidireccionales (permitir + restringir)**: rechazado de plano — viola
  el principio sagrado del Veto ([[DEC-001]]).

## References

- [[DEC-005]] — Mente Portable: Audit Chain NDJSON (fuente de verdad).
- [[DEC-003]] — SQLite Schema (la migración 002 añade `ontology_rules`).
- [[DEC-001]] — Stack Fase 1 Lockdown (Magister es stdlib-only, sin deps nuevas).
- `agents/advisory/magister.py`, `core/risk/ontology.py`,
  `infra/sqlite/migrations/002_ontology_rules.sql`,
  `.claude/skills/magister/SKILL.md`, `tests/integration/test_magister_ontology_e2e.py`
