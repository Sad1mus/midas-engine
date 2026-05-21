---
id: DEC-009
title: Recuperacion Semantica por Embeddings (RAG) - PROPUESTA
type: decision
status: proposed
owner: Jordy Marin
created: '2026-05-21'
---

# DEC-009: Recuperacion Semantica por Embeddings (RAG) - PROPUESTA

> **STATUS: proposed.** Esta es una PROPUESTA redactada por el agente. Implica una
> **dependencia nueva**, así que su aceptación es una decisión **humana** de los
> socios (DEC-001 lockdown). El agente NO la implementa ni instala nada; este
> documento solo expone la opción para que un humano la acepte o rechace.

## Context

La capa de recuperación actual ([[DEC-007]], `core/knowledge/retrieval.py`) es
**léxica**: BM25 (FTS5) + estructura + grafo. Encuentra notas que comparten
*palabras* con la query, pero no las que comparten *significado*. Ej.: una query
"miedo en el mercado" no recupera una nota sobre "spikes de volatilidad y risk-off"
aunque sean el mismo concepto. A medida que el vault crece (post-mortems, regímenes,
ontología), esta brecha semántica limita el valor del cerebro.

La recuperación semántica por **embeddings** (RAG) cerraría esa brecha. Pero añade
una dependencia fuera del stack Fase 1, por lo que requiere un DEC aceptado por un
humano antes de tocar `pyproject`.

## Decision (PROPUESTA — requiere aceptación humana)

Añadir un índice vectorial semántico **complementario** (no sustituto) al léxico:

### Modelo de embeddings (open-source, local, sin API de pago)

- **Candidato A — `bge-small-en-v1.5`** (~33M parámetros, 384 dims): rápido en CPU,
  buena calidad retrieval, licencia MIT. ~130 MB.
- **Candidato B — `all-MiniLM-L6-v2`** (sentence-transformers, 384 dims): el estándar
  de facto, muy ligero (~90 MB), Apache-2.0.
- Recomendación: **B (MiniLM)** para v1 por peso/madurez; multilingüe (`paraphrase-
  multilingual-MiniLM`) si el vault mezcla ES/EN.

### Encaje en SQLite (dos opciones)

1. **Brute-force coseno (v1, cero deps extra de DB):** guardar los vectores como
   BLOB en una tabla `notes_vec(path, dim, vector)`; en query, traer todos los
   vectores y rankear por coseno en numpy. Viable hasta ~10⁴–10⁵ notas (el vault
   está muy por debajo). **Reusa el stack ya presente (numpy).**
2. **`sqlite-vec` (escala futura):** extensión SQLite para búsqueda vectorial nativa
   (KNN). Mejor a gran escala, pero es una dependencia/extensión nueva adicional.

Recomendación: empezar con **(1) brute-force**, migrar a (2) solo si el vault crece.

### Dependencia nueva (lo que dispara este DEC)

- `sentence-transformers` (+ `torch` CPU) **o** `fastembed` (ONNX, mucho más ligero,
  sin torch). Recomendado **`fastembed`** por huella mínima y arranque rápido.
- Coste: ~0 monetario (modelo local, inferencia CPU). Coste real = peso del paquete
  (~) y tiempo de embedding del vault (segundos para cientos de notas).

### Integración

- Un comando `reindex --semantic` calcula embeddings de los cuerpos y los persiste.
- `retrieve()` fusiona el score léxico (BM25) con el semántico (coseno) — *hybrid
  search* (p. ej. Reciprocal Rank Fusion), preservando estructura y grafo.

## Consequences

### Positive

- Recuperación por significado, no solo por palabras → cerebro más útil.
- Brute-force reusa numpy (ya en el stack); arranque sin infra nueva de DB.

### Negative / Trade-offs

- Dependencia nueva (`fastembed`/`sentence-transformers`) → peso y superficie.
- Embeddings hay que recomputar al cambiar de modelo; versionar el modelo usado.

### Risks

- **Drift de modelo/licencia** → mitigado: fijar versión y licencia (MIT/Apache).
- **Falsa sensación de relevancia** → mitigado: fusión híbrida con BM25, no sustituir.

## Criterios de aceptación (para el humano)

1. Decidir modelo (MiniLM vs bge-small vs multilingüe) y paquete (`fastembed` vs
   `sentence-transformers`).
2. Aprobar la dependencia nueva en `pyproject` (esto **levanta** el lockdown solo
   para ese paquete).
3. Definir umbral de calidad: en un set de queries de prueba, la fusión híbrida debe
   superar a BM25 solo en recall@5.
4. Confirmar que corre offline en CPU dentro del presupuesto de latencia del cerebro
   (no en el hot path de ejecución; el vault no está en el hot path — ver [[DEC-004]]).

## Alternatives Considered

- **Quedarse solo con BM25 ([[DEC-007]]):** opción por defecto si se rechaza este DEC;
  cero deps, pero sin semántica.
- **API de embeddings de pago (OpenAI/Cohere):** rechazada — fuente externa y coste
  recurrente; contradice el espíritu local/offline de Fase 1.

## References

- [[DEC-001]] — Stack Fase 1 Lockdown (por qué esto necesita decisión humana).
- [[DEC-004]] — Convenciones del Vault (el vault no está en el hot path).
- [[DEC-007]] — Capa de Recuperación léxica actual (BM25 + grafo) que esto extiende.
- `core/knowledge/retrieval.py`
