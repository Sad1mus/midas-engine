# Cockpit de MIDAS — guía de uso

El **cockpit** te deja *ver* el estado vivo de MIDAS en Obsidian: equity y PnL,
el estado del **DD-gate**, trades (filled vs vetoed), sleeves con su veredicto
**GO/NO-GO**, regímenes **𝒳**, reglas de **Magister** y decisiones (DEC). Todo se
**proyecta desde SQLite** (read-only) a notas Markdown; Obsidian + Dataview las
agregan en la nota índice `MIDAS — Cockpit.md`.

> ⚠️ Las notas bajo `_generated/` y `MIDAS — Cockpit.md` están **generadas**: no las
> edites a mano, se sobrescriben. Las notas de `10_Decisions/` son **humanas** y el
> cockpit nunca las toca.

## 1. Abrir Obsidian sobre el vault

1. Instala [Obsidian](https://obsidian.md).
2. **Open folder as vault** → elige la carpeta `vault/` de este repo.

## 2. Instalar el plugin Dataview

1. Settings → Community plugins → **Browse**.
2. Busca **Dataview** (de blacksmithgu) → Install → **Enable**.
3. (Recomendado) Dataview settings → activar **Enable JavaScript Queries** no es
   necesario; el cockpit usa solo bloques `dataview` (DQL), no `dataviewjs`.

## 3. Regenerar la proyección

Cada vez que quieras refrescar el cockpit con el último estado del `.db`:

```bash
cd ~/midas
source .venv/bin/activate
python scripts/cockpit.py          # reconstruye el .db desde el chain y proyecta a vault/
```

El comando imprime un resumen (notas por tipo, estado del gate, sleeves GO/NO-GO) y
es **idempotente**: correrlo dos veces no cambia el árbol. Abre luego
`00_System/cockpit/MIDAS — Cockpit.md` en Obsidian.

## 4. Qué es cada cosa

| Sección | De dónde sale | Qué te dice |
|---|---|---|
| 🛡️ DD-gate | `risk_gates_state` | Nivel del gate y `sizing_mult` (≤ 1) |
| 💰 Equity / PnL | `equity_curve` | Equity, pico, drawdown |
| 📈 Trades | `trades` | Recientes, filled vs vetoed |
| 🧪 Sleeves | `backtest_results` | Veredicto GO/NO-GO (DSR, PBO) |
| 🌀 Regímenes 𝒳 | `regime_states` | Etiqueta TDA y `D_topo` |
| ⚖️ Reglas Magister | `ontology_rules` | Reglas activas (solo restringen) |
| 📜 Decisiones | `10_Decisions/` | ADRs recientes |

La fuente de verdad es el **hash chain** (`infra/audit/chain.ndjson`); el `.db` y
estas notas son **vistas reconstruibles**.
