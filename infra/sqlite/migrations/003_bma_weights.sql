-- =====================================================================
-- MIDAS Capital — Migration 003: bma_weights
--
-- Pesos de Bayesian Model Averaging sobre las señales de N sleeves (Round 6).
-- El BMA NO decide tamaño final: su salida es un peso por sleeve que pondera
-- la señal combinada, la cual sigue pasando por el Veto/DD-gates como cualquier
-- otra (principio sagrado — el Risk Manager nunca se evita; ver CLAUDE.md §1).
--
-- Cada fila ancla al hash chain por audit_id (event_type='bma'), igual que
-- backtest_results (DEC-005). Una corrida de BMA escribe N filas que comparten
-- run_id; los pesos de un run_id suman 1.
--
-- Convenciones (heredadas de 001_initial.sql / DEC-003):
--   * Timestamps ISO-8601 UTC en TEXT + unix_ms redundante en INTEGER.
--   * NO boolean. FK a audit_log.id para trazabilidad.
-- =====================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS bma_weights (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc        TEXT    NOT NULL,                 -- instante del cómputo, ISO-8601 UTC
    ts_ms         INTEGER NOT NULL,                 -- mismo instante, unix ms
    run_id        TEXT    NOT NULL,                 -- agrupa las N filas de una corrida de BMA
    sleeve_id     TEXT    NOT NULL,                 -- sleeve ponderado
    weight        REAL    NOT NULL CHECK (weight >= 0 AND weight <= 1),  -- posterior, Σ=1 por run_id
    prior         REAL    NOT NULL CHECK (prior  >= 0 AND prior  <= 1),  -- prior usado
    log_evidence  REAL    NOT NULL,                 -- log Bayes-factor (skill vs no-skill, unit-info)
    post_mean     REAL    NOT NULL,                 -- media posterior del retorno por barra (shrunk a 0)
    post_z        REAL    NOT NULL,                 -- z-score posterior de skill (post_mean / sd posterior)
    sharpe        REAL    NOT NULL,                 -- Sharpe muestral OOS de la serie del sleeve
    n_obs         INTEGER NOT NULL CHECK (n_obs >= 0),  -- nº de retornos OOS usados
    audit_id      INTEGER NOT NULL REFERENCES audit_log (id),  -- ancla al hash chain
    UNIQUE (run_id, sleeve_id)
);

CREATE INDEX IF NOT EXISTS ix_bma_weights_run    ON bma_weights (run_id);
CREATE INDEX IF NOT EXISTS ix_bma_weights_sleeve ON bma_weights (sleeve_id, ts_ms DESC);
