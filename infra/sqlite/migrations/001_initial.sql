-- =====================================================================
-- MIDAS Capital — SQLite Schema v1 (initial migration)
-- Generated for Day 0 setup. Subsequent changes go in new migration files
-- under infra/sqlite/migrations/NNN_*.sql, never edit this file in-place.
--
-- Principles:
--   1. Append-only where possible (audit_log, trades, executions, equity_curve).
--   2. SHA-256 hash chain on audit_log (Merkle-like). prev_hash = hash of
--      previous row's content; first row prev_hash = 64 zeros.
--   3. All timestamps ISO-8601 strings in UTC (TEXT), redundant unix_ms
--      (INTEGER) for fast range queries.
--   4. NO boolean type — use INTEGER 0/1 with CHECK constraints.
--   5. Every state-changing table has FK to audit_log.id when applicable.
-- =====================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;

-- ---------------------------------------------------------------------
-- schema_version — single-row table for migration tracking
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    version         INTEGER NOT NULL,
    applied_at_utc  TEXT    NOT NULL,
    applied_at_ms   INTEGER NOT NULL,
    git_commit      TEXT    NULL
);

-- ---------------------------------------------------------------------
-- audit_log — append-only hash chain. Every state-changing action writes here.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT    NOT NULL,
    ts_ms           INTEGER NOT NULL,
    actor           TEXT    NOT NULL,         -- 'system' | 'human:<name>' | 'agent:<name>'
    event_type      TEXT    NOT NULL,         -- 'decision' | 'trade' | 'execution' | 'regime_change' | 'gate_trip' | 'override'
    event_key       TEXT    NULL,             -- e.g. 'DEC-007' or 'trade:abc123'
    payload_json    TEXT    NOT NULL,         -- JSON serialized event data
    payload_hash    TEXT    NOT NULL,         -- SHA-256(payload_json), hex lowercase
    prev_hash       TEXT    NOT NULL,         -- hash of previous row, 64 zeros for first
    chain_hash      TEXT    NOT NULL UNIQUE,  -- SHA-256(prev_hash || payload_hash)
    CHECK (length(payload_hash) = 64),
    CHECK (length(prev_hash)    = 64),
    CHECK (length(chain_hash)   = 64)
);

CREATE INDEX IF NOT EXISTS ix_audit_ts_ms      ON audit_log (ts_ms);
CREATE INDEX IF NOT EXISTS ix_audit_event_type ON audit_log (event_type);
CREATE INDEX IF NOT EXISTS ix_audit_event_key  ON audit_log (event_key);

-- ---------------------------------------------------------------------
-- decisions — index of vault/10_Decisions/*.md notes
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS decisions (
    id              TEXT    PRIMARY KEY,      -- 'DEC-001', etc.
    title           TEXT    NOT NULL,
    status          TEXT    NOT NULL CHECK (status IN ('proposed','accepted','superseded','rejected')),
    owner           TEXT    NOT NULL,
    created_utc     TEXT    NOT NULL,
    created_ms      INTEGER NOT NULL,
    superseded_by   TEXT    NULL REFERENCES decisions (id),
    vault_path      TEXT    NOT NULL,         -- relative to vault/, e.g. '10_Decisions/DEC-001_Stack-F1-Lockdown.md'
    content_hash    TEXT    NOT NULL,         -- SHA-256 of note body (excluding frontmatter)
    audit_id        INTEGER NOT NULL REFERENCES audit_log (id),
    CHECK (length(content_hash) = 64)
);

CREATE INDEX IF NOT EXISTS ix_decisions_status ON decisions (status);

-- ---------------------------------------------------------------------
-- sleeves — registry of strategy sleeves (futures_tda, equity_statarb, etc.)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sleeves (
    id              TEXT    PRIMARY KEY,      -- e.g. 'futures_tda_v1'
    name            TEXT    NOT NULL,
    track           TEXT    NOT NULL CHECK (track IN ('A','B')),
    status          TEXT    NOT NULL CHECK (status IN ('research','paper','live','paused','retired')),
    asset_class     TEXT    NOT NULL,         -- 'futures' | 'equity' | 'crypto' | 'options'
    instruments     TEXT    NOT NULL,         -- JSON array, e.g. '["MNQ","MES"]'
    vault_path      TEXT    NULL,
    dsr             REAL    NULL,
    pbo             REAL    NULL,
    target_vol      REAL    NULL,
    created_utc     TEXT    NOT NULL,
    activated_utc   TEXT    NULL,
    retired_utc     TEXT    NULL,
    CHECK (dsr IS NULL OR dsr  >= -10),
    CHECK (pbo IS NULL OR (pbo >= 0 AND pbo <= 1))
);

-- ---------------------------------------------------------------------
-- trades — every trade attempted (signal generated, may or may not have filled)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trades (
    id              TEXT    PRIMARY KEY,      -- ULID
    sleeve_id       TEXT    NOT NULL REFERENCES sleeves (id),
    track           TEXT    NOT NULL CHECK (track IN ('A','B')),
    ts_signal_utc   TEXT    NOT NULL,
    ts_signal_ms    INTEGER NOT NULL,
    instrument      TEXT    NOT NULL,
    side            TEXT    NOT NULL CHECK (side IN ('long','short','flat')),
    size_target     REAL    NOT NULL,
    entry_target    REAL    NULL,
    stop_target     REAL    NULL,
    take_target     REAL    NULL,
    regime_id       INTEGER NULL,            -- FK regime_states.id
    score_bma       REAL    NULL,            -- BMA score at signal time
    status          TEXT    NOT NULL CHECK (status IN ('proposed','approved','vetoed','filled','partial','cancelled','expired')),
    veto_reason     TEXT    NULL,
    audit_id        INTEGER NOT NULL REFERENCES audit_log (id)
);

CREATE INDEX IF NOT EXISTS ix_trades_sleeve  ON trades (sleeve_id, ts_signal_ms);
CREATE INDEX IF NOT EXISTS ix_trades_status  ON trades (status);

-- ---------------------------------------------------------------------
-- executions — actual fills (1..N per trade for partials)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS executions (
    id              TEXT    PRIMARY KEY,
    trade_id        TEXT    NOT NULL REFERENCES trades (id),
    ts_fill_utc     TEXT    NOT NULL,
    ts_fill_ms      INTEGER NOT NULL,
    instrument      TEXT    NOT NULL,
    side            TEXT    NOT NULL CHECK (side IN ('long','short')),
    size_filled     REAL    NOT NULL,
    price_fill      REAL    NOT NULL,
    commission      REAL    NOT NULL DEFAULT 0,
    fees            REAL    NOT NULL DEFAULT 0,
    slippage_ticks  REAL    NULL,
    broker          TEXT    NOT NULL,         -- 'tradovate' | 'ibkr' | 'topstep_paper' | 'paper_b'
    venue_order_id  TEXT    NULL,
    audit_id        INTEGER NOT NULL REFERENCES audit_log (id)
);

CREATE INDEX IF NOT EXISTS ix_executions_trade ON executions (trade_id);
CREATE INDEX IF NOT EXISTS ix_executions_ts    ON executions (ts_fill_ms);

-- ---------------------------------------------------------------------
-- equity_curve — time series of NAV per sleeve and aggregate
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS equity_curve (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT    NOT NULL,
    ts_ms           INTEGER NOT NULL,
    sleeve_id       TEXT    NULL REFERENCES sleeves (id), -- NULL = aggregate portfolio
    track           TEXT    NOT NULL CHECK (track IN ('A','B','agg')),
    equity          REAL    NOT NULL,
    realized_pnl    REAL    NOT NULL DEFAULT 0,
    unrealized_pnl  REAL    NOT NULL DEFAULT 0,
    peak_equity     REAL    NOT NULL,        -- running max
    drawdown_pct    REAL    NOT NULL,         -- (peak - equity) / peak; 0..1
    UNIQUE (ts_ms, sleeve_id, track)
);

CREATE INDEX IF NOT EXISTS ix_equity_ts ON equity_curve (ts_ms);

-- ---------------------------------------------------------------------
-- regime_states — output of regime classifier (TDA + baseline)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS regime_states (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT    NOT NULL,
    ts_ms           INTEGER NOT NULL,
    classifier      TEXT    NOT NULL,        -- 'tda_v1' | 'hmm_baseline' | 'ensemble'
    regime_label    TEXT    NOT NULL,        -- string label, classifier-specific
    posterior_json  TEXT    NOT NULL,        -- JSON dict of P(regime|x)
    features_json   TEXT    NULL,            -- JSON of input features for reproducibility
    UNIQUE (ts_ms, classifier)
);

CREATE INDEX IF NOT EXISTS ix_regime_ts ON regime_states (ts_ms);

-- ---------------------------------------------------------------------
-- agent_decisions — every BMA input from every agent
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT    NOT NULL,
    ts_ms           INTEGER NOT NULL,
    agent           TEXT    NOT NULL,        -- 'signal_core' | 'risk_core' | etc.
    trade_id        TEXT    NULL REFERENCES trades (id),
    score           REAL    NOT NULL,        -- normalized score [-1, 1] or [0, 1] depending on agent
    confidence      REAL    NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    rationale_json  TEXT    NULL,            -- structured rationale (for audit / Obsidian sync)
    weight_used     REAL    NULL             -- BMA weight assigned given regime
);

CREATE INDEX IF NOT EXISTS ix_agent_decisions_trade ON agent_decisions (trade_id);
CREATE INDEX IF NOT EXISTS ix_agent_decisions_ts    ON agent_decisions (ts_ms);

-- ---------------------------------------------------------------------
-- risk_gates_state — current DD gate state, sizing multipliers
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_gates_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT    NOT NULL,
    ts_ms           INTEGER NOT NULL,
    current_dd_pct  REAL    NOT NULL,
    gate_level      TEXT    NOT NULL CHECK (gate_level IN ('normal','dd5','dd10','dd15','halt')),
    sizing_mult     REAL    NOT NULL,         -- 1.0 | 0.5 | 0.25 | 0.125 | 0.0
    trigger_audit_id INTEGER NULL REFERENCES audit_log (id),
    notes           TEXT    NULL
);

CREATE INDEX IF NOT EXISTS ix_risk_gates_ts ON risk_gates_state (ts_ms);

-- ---------------------------------------------------------------------
-- cost_model_calibrations — Almgren-Chriss params, slippage by instrument / hour
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cost_model_calibrations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT    NOT NULL,
    ts_ms           INTEGER NOT NULL,
    instrument      TEXT    NOT NULL,
    hour_utc        INTEGER NOT NULL CHECK (hour_utc >= 0 AND hour_utc < 24),
    spread_ticks_mean REAL  NOT NULL,
    spread_ticks_p95  REAL  NOT NULL,
    impact_eta      REAL    NOT NULL,         -- Almgren-Chriss temporary impact coef
    impact_gamma    REAL    NOT NULL,         -- permanent impact coef
    slippage_var    REAL    NOT NULL,
    n_samples       INTEGER NOT NULL,
    UNIQUE (instrument, hour_utc, ts_ms)
);

-- ---------------------------------------------------------------------
-- backtest_results — CPCV outputs per sleeve candidate
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS backtest_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sleeve_id       TEXT    NOT NULL,        -- may not yet be in sleeves table if research
    git_commit      TEXT    NOT NULL,
    ts_utc          TEXT    NOT NULL,
    ts_ms           INTEGER NOT NULL,
    n_paths         INTEGER NOT NULL CHECK (n_paths >= 10),
    sharpe_mean     REAL    NOT NULL,
    sharpe_std      REAL    NOT NULL,
    dsr             REAL    NOT NULL,         -- Deflated Sharpe Ratio
    pbo             REAL    NOT NULL CHECK (pbo >= 0 AND pbo <= 1),
    max_dd_pct      REAL    NOT NULL,
    calmar          REAL    NULL,
    n_trades_mean   INTEGER NULL,
    embargo_pct     REAL    NOT NULL,
    purge_pct       REAL    NOT NULL,
    config_json     TEXT    NOT NULL,         -- full config snapshot for reproducibility
    audit_id        INTEGER NOT NULL REFERENCES audit_log (id)
);

CREATE INDEX IF NOT EXISTS ix_backtest_sleeve ON backtest_results (sleeve_id, ts_ms DESC);
