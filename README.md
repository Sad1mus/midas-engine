# MIDAS Engine

> **Infraestructura de trading cuantitativo *audit-grade*, neuro-simbólica y verificable.**
> Núcleo de riesgo determinista · cadena de auditoría criptográfica · validación anti-overfitting ·
> conocimiento humano gobernado por reglas.

Este repositorio es una **vitrina de ingeniería** (sanitizada) del motor MIDAS: el diseño de sistemas,
el rigor científico y las garantías de seguridad detrás de un sistema de trading automatizado. No
contiene estrategias propietarias, claves ni datos de producción.

---

## Honestidad por delante

Esto **no** es "un bot que gana dinero". Es la **infraestructura disciplinada** sobre la que un edge
puede descubrirse y —sobre todo— **probarse honestamente o descartarse**. A día de hoy:

- ✅ Infraestructura audit-grade, núcleo de riesgo, gobernanza y validación: **funcionando, con tests y CI**.
- ⏳ Una estrategia con *edge* probado y *track record*: **pendiente, por diseño**.

Esa transparencia es deliberada: el sistema entero está construido para **rechazar el auto-engaño**
(el peor enemigo de un cuant), no para venderlo.

## Por qué es interesante (perfil híbrido: IA × finanzas cuantitativas × sistemas verificables)

| Pilar | Qué demuestra |
|---|---|
| 🔗 **Mente portable** — hash chain NDJSON (SHA-256) | *Verifiable computing*: una fuente de verdad inmutable y auditable; el estado se reconstruye por *replay*. Relevante para cripto/Web3. |
| 🛡️ **Núcleo de riesgo / Veto** | Capa determinista que ninguna señal puede aflojar. *Drawdown-first*: sobrevivir antes que rentar. |
| 🧠 **Magister** — escritor único de reglas ontológicas | Inyección de conocimiento humano **gobernada**: las reglas solo restringen (`veto`/`size_down`), nunca aprueban. |
| 🔬 **Anti-overfitting** — CPCV → DSR → PBO | El juez honesto: GO/NO-GO de cada estrategia con metodología publicada, no con un backtest ingenuo. |
| 🧩 **Variable 𝒳 (TDA)** | Detección de régimen de mercado por homología persistente (análisis topológico de datos). |
| 🤝 **Gobernanza por ADRs** | Cada decisión de arquitectura firmada y versionada (`vault/10_Decisions/`). |

## Arquitectura (vista de pájaro)

```text
   datos ─▶ Variable 𝒳 (régimen) ─▶ señal de sleeve ─▶ ┌──────────────┐
                                                        │    VETO       │ ◀── reglas de Magister
   conocimiento humano ─▶ Magister ─▶ ontología ──────▶ │ (determinista)│     (solo restringen)
                                                        └──────┬────────┘
                                                               ▼
                          cadena de auditoría (fuente de verdad) ─▶ proyección SQLite / Obsidian
                                                               │
                                       validación CPCV→DSR→PBO ─▶ veredicto GO / NO-GO
```

- **`core/`** — riesgo (Veto, gates, kill-switch, ontología), modelos, config (todo desde `os.environ`).
- **`agents/`** — capa advisory (Magister).
- **`research/`** — `cpcv`, `deflated_sharpe`, `pbo`, `cost_model`.
- **`infra/`** — cadena de auditoría, SQLite, sincronización Obsidian.
- **`sleeves/`** — estrategias (interfaz; sin lógica de edge propietaria).
- **`scripts/`** — `init_db`, `decision_log`, `verify_chain`, `cockpit`.
- **`tests/`** — suite con fixtures de datos **públicos**; ninguna llamada de red en CI.

## Calidad de ingeniería

- **CI** (`.github/workflows/ci.yml`): `ruff` + `ruff format --check` + `mypy` + `pytest` en Python 3.12.
- **Stack lockdown**: ninguna dependencia entra sin un ADR que la justifique.
- **Sin secretos en el repo**: config 100% por entorno; `.gitignore` blinda claves, `.db` y datos.
- **Hot path en Rust** (`Cargo.toml`): workspace reservado para reescribir el camino crítico vía PyO3
  cuando la latencia lo exija (hoy todo en Python, por velocidad de iteración).

## Quick start

```bash
pip install -e ".[dev]"     # o: uv sync
python scripts/init_db.py    # inicializa el SQLite (reconstruible desde la cadena)
pytest -q                    # suite verde
python scripts/verify_chain.py   # integridad de la cadena de auditoría
```

## Estado

Showcase sanitizado de un proyecto en construcción. El cerebro del fondo (estrategias, hipótesis,
*track record*) se mantiene **privado**. Aquí se muestra **cómo está construido**, no **qué opera**.

---

© Zenithstone Holding — Todos los derechos reservados. Ver [`LICENSE`](LICENSE).
