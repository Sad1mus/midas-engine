---
id: DEC-014
title: R6 BMA Bayesiano e infra de senales - gate de hipotesis humana, sin edge nuevo
type: decision
status: accepted
owner: Jordy Marin
created: '2026-05-21'
---

# DEC-014: R6 — BMA Bayesiano + gate de hipótesis humana (sin edge nuevo)

## Context

Tras [[DEC-013]] (R5: datos reales, GLD GO / QQQ NO-GO, PnL negativo), el Round 6
buscaba pasar de "el pipeline corre sobre datos reales" a **"¿hay EDGE?"**: combinar
varios sleeves con **BMA** (Bayesian Model Averaging) y validar honestamente.

Decisión de diseño DURA del round: el **núcleo intelectual** —las hipótesis de
entrada/salida de oro (GC) y NASDAQ (NQ)— lo ponen los **HUMANOS** vía `/magister`;
el agente solo construye la plomería (BMA) y la valida sin sesgo. Las tareas de los
sleeves arrancan con una **guarda de gate**: exigen que exista en el chain de
producción una regla `ontology_rule` con actor `human:*` documentando la tesis del
instrumento. Sin esa hipótesis, el sleeve NO se implementa — inventarla sería
exactamente el sobreajuste que CPCV→DSR→PBO existe para rechazar (ver [[DEC-006]],
[[DEC-008]]).

## Decision

Se consolida la infraestructura de BMA y se deja constancia honesta del resultado del
round: **no se buscó ni se encontró edge nuevo, porque las hipótesis humanas de entrada
no fueron inyectadas.**

### Infra de BMA — `agents/signal/bma.py` (tarea 1, DONE)

`bma_weights(returns)` combina las señales OOS de N sleeves con un **prior escéptico**
μ_i ~ Normal(0, τ²) (no asumimos edge a priori) y likelihood Normal. Pondera por el
**log Bayes-factor unilateral de información unitaria** `0.5·max(z_post, 0)²`, donde
`z_post` es la señal/ruido posterior de skill: un sleeve sin drift positivo no recibe
evidencia (cae al prior). Pesos = softmax estable → suman 1. **SOLO numpy/scipy** (stack
lockdown, [[DEC-001]]). Invariante sagrado: el BMA **no decide tamaño**; su salida sigue
pasando por el Veto/DD-gates como cualquier señal.

Persistencia: migración `003_bma_weights.sql` → tabla `bma_weights` anclada al hash
chain (`event_type='bma'`, fuente de verdad — [[DEC-005]]). Tests (`tests/test_bma.py`,
8 casos): el BMA asigna más peso a la mejor serie, los pesos suman 1, el prior se respeta
y se persiste correctamente. Schema → **v3**.

### Sleeves oro y NASDAQ — [BLOCKED] (tareas 2 y 3)

Ambas guardas de gate **fallaron**: el chain de producción tiene **0 eventos
`ontology_rule`** y, por tanto, ninguna hipótesis humana de GC/oro ni de NQ/NASDAQ.
Por diseño, **no se escribió código de señal**. Desbloqueo: el socio inyecta su tesis
vía `/magister` y se re-corre el round.

### Veredictos por sleeve y de cartera — sin resultados (tareas 4 y 5)

| nivel | resultado R6 |
|---|---|
| sleeves nuevos validados | 0 (oro y NASDAQ [blocked]) |
| veredictos GO/NO-GO nuevos | 0 — no se escribe fila en `backtest_results` |
| portafolio BMA (combinar GO) | conjunto de GO = ∅ → **"aún no hay edge"** (válido) |

Las 2 filas previas de R5 (`gld_gc_real` DSR=0.913/PBO=0.183; `qqq_nq_real`
DSR=0.460/PBO=0.901) quedan **intactas**. El cockpit se refrescó (1 GO/1 NO-GO,
DD-gate dd10 ×0.25, equity 45 831, DD 0.107).

### Estado del edge

**Edge probado = sigue PENDIENTE.** No se forzó ningún GO ni se fabricó ninguna fila.
El round entregó infraestructura (BMA + tabla + gate de hipótesis humana) y un
resultado honesto: sin tesis humana, no hay sleeve; sin sleeve, no hay edge que medir.

## Consequences

### Positive

- BMA bayesiano listo, testeado y auditable; el día que haya ≥2 sleeves GO, la cartera
  se combina sin sesgo y pasa por el Veto.
- La **guarda de hipótesis humana** quedó probada en producción: el agente respetó el
  límite y no inventó una señal de entrada (anti-overfitting estructural, no solo numérico).

### Negative / Trade-offs

- El round no produjo sleeves nuevos: el progreso del edge depende de un input humano
  (`/magister`) que aún no llegó.

### Risks

- **Confundir "infra lista" con "edge"** → mitigado: este DEC deja explícito que el edge
  sigue pendiente y que 0 veredictos GO se emitieron en R6.

## Alternatives Considered

- **Inventar una hipótesis de oro/NASDAQ** para "tener sleeves": rechazado de plano —
  viola el gate humano y el espíritu anti-overfitting. [BLOCKED] es el resultado correcto.
- **Combinar el único GO histórico (R5 GLD) como "cartera"**: rechazado — una cartera BMA
  de un solo sleeve es degenerada y no es un GO de cartera de R6; reportar "no hay edge".

## References

- [[DEC-013]] — R5 datos reales (punto de partida, veredictos GLD/QQQ).
- [[DEC-008]] — Pipeline anti-overfitting CPCV→DSR→PBO (el que NO se forzó).
- [[DEC-006]] — Magister, escritor único de reglas (la vía de la hipótesis humana).
- [[DEC-005]] — Chain NDJSON como fuente de verdad (ancla de `bma_weights`).
- [[DEC-001]] — Stack lockdown (BMA = solo numpy/scipy).
- `agents/signal/bma.py`, `infra/sqlite/migrations/003_bma_weights.sql`, `tests/test_bma.py`.
