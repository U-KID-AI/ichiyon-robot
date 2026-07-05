-- v3.0 admin bot switching, user management, and per-bot voice lines.
-- Additive only: no existing rows are changed or deleted by this migration.

ALTER TABLE admin_users
    ADD COLUMN IF NOT EXISTS display_name TEXT;

ALTER TABLE admin_users
    ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE admin_users
    ADD COLUMN IF NOT EXISTS can_manage_users BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE admin_users
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS bot_voice_lines (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bot_id TEXT NOT NULL DEFAULT 'ichiyon' REFERENCES bot_instances(bot_id),
    guild_id TEXT NOT NULL REFERENCES guilds(guild_id),
    join_line TEXT,
    revive_line TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by_discord_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bot_id, guild_id)
);

CREATE INDEX IF NOT EXISTS idx_bot_voice_lines_bot_guild
    ON bot_voice_lines(bot_id, guild_id);
