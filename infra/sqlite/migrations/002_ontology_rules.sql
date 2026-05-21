-- =====================================================================
-- MIDAS Capital — Migration 002: ontology_rules
--
-- Reglas ontológicas ADITIVAS inyectadas por humano (vía Magister, ver DEC-006).
-- Principio sagrado: estas reglas SOLO restringen. La acción ∈ {veto, size_down};
-- jamás aprueban algo que el Veto rechazaría (el Risk Manager las aplica DESPUÉS
-- de sus reglas duras, nunca antes). Cada regla queda anclada al hash chain por
-- audit_id (ver DEC-005, infra/audit/chain.ndjson) y proyectada al vault.
--
-- Convenciones (heredadas de 001_initial.sql / DEC-003):
--   * Timestamps ISO-8601 UTC en TEXT + unix_ms redundante en INTEGER.
--   * NO boolean — INTEGER 0/1 con CHECK.
--   * FK a audit_log.id para trazabilidad.
-- =====================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS ontology_rules (
    id              TEXT    PRIMARY KEY,        -- slug/ULID, e.g. 'rule_fomc_nq_freeze'
    ts_utc          TEXT    NOT NULL,           -- alta de la regla, ISO-8601 UTC
    ts_ms           INTEGER NOT NULL,           -- mismo instante, unix ms (consultas de rango)
    rule_type       TEXT    NOT NULL,           -- 'event_window' | 'time_window' | 'instrument_block' | ...
    instrument      TEXT    NULL,               -- e.g. 'NQ'; NULL = aplica a todos los instrumentos
    window_spec     TEXT    NULL,               -- ventana, JSON (ej. {"event":"FOMC","pre_min":30,"post_min":30})
    action          TEXT    NOT NULL CHECK (action IN ('veto','size_down')),  -- SOLO restringe
    params_json     TEXT    NOT NULL DEFAULT '{}',  -- params de la acción (ej. {"size_mult":0.5})
    audit_id        INTEGER NOT NULL REFERENCES audit_log (id),  -- ancla al hash chain
    vault_path      TEXT    NULL,               -- relativo a vault/, nota proyectada por Magister
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE INDEX IF NOT EXISTS ix_ontology_rules_active     ON ontology_rules (active);
CREATE INDEX IF NOT EXISTS ix_ontology_rules_instrument ON ontology_rules (instrument);
CREATE INDEX IF NOT EXISTS ix_ontology_rules_ts         ON ontology_rules (ts_ms);
