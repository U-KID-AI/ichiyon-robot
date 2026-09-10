-- Add bot/guild-scoped persona draw presets.
-- Non-destructive: creates new tables and indexes only.

CREATE TABLE IF NOT EXISTS persona_draws (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL DEFAULT 'ichiyon',
    guild_id TEXT NOT NULL,
    name TEXT NOT NULL,
    trigger_text TEXT NOT NULL,
    trigger_key TEXT NOT NULL,
    reroll_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    reroll_probability_percent INTEGER NOT NULL DEFAULT 10,
    max_rerolls INTEGER NOT NULL DEFAULT 1,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT persona_draws_reroll_probability_check
        CHECK (reroll_probability_percent >= 0 AND reroll_probability_percent <= 100),
    CONSTRAINT persona_draws_max_rerolls_check
        CHECK (max_rerolls >= 0 AND max_rerolls <= 10)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_draws_bot_guild_trigger_key_unique
    ON persona_draws (bot_id, guild_id, trigger_key);

CREATE INDEX IF NOT EXISTS idx_persona_draws_bot_guild_enabled
    ON persona_draws (bot_id, guild_id, enabled);

CREATE TABLE IF NOT EXISTS persona_draw_candidates (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL DEFAULT 'ichiyon',
    guild_id TEXT NOT NULL,
    persona_draw_id BIGINT NOT NULL REFERENCES persona_draws(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_draw_candidates_draw_body_unique
    ON persona_draw_candidates (bot_id, guild_id, persona_draw_id, body);

CREATE INDEX IF NOT EXISTS idx_persona_draw_candidates_draw_enabled
    ON persona_draw_candidates (bot_id, guild_id, persona_draw_id, enabled, sort_order);

CREATE TABLE IF NOT EXISTS persona_draw_reroll_lines (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL DEFAULT 'ichiyon',
    guild_id TEXT NOT NULL,
    persona_draw_id BIGINT NOT NULL REFERENCES persona_draws(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_draw_reroll_lines_draw_body_unique
    ON persona_draw_reroll_lines (bot_id, guild_id, persona_draw_id, body);

CREATE INDEX IF NOT EXISTS idx_persona_draw_reroll_lines_draw_enabled
    ON persona_draw_reroll_lines (bot_id, guild_id, persona_draw_id, enabled, sort_order);
