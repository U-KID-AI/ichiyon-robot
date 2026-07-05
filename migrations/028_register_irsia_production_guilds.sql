-- Register the irsia bot instance and its initial production guilds.
-- Additive only: no existing rows are changed or deleted by this migration.

INSERT INTO bot_instances (bot_id, display_name, description, enabled, token_env_key)
VALUES (
    'irsia',
    'イルシア',
    'v3.0で追加するBotインスタンス',
    TRUE,
    'IRSIA_DISCORD_TOKEN'
)
ON CONFLICT (bot_id) DO NOTHING;

INSERT INTO guilds (guild_id, name, enabled)
VALUES
    ('1520964851046944900', '天使の聖域', TRUE),
    ('928619302213533736', '神聖イルシア皇国', TRUE)
ON CONFLICT (guild_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS bot_guilds (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bot_instances(bot_id) ON DELETE CASCADE,
    guild_id TEXT NOT NULL REFERENCES guilds(guild_id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (bot_id, guild_id)
);

CREATE INDEX IF NOT EXISTS idx_bot_guilds_bot_enabled
    ON bot_guilds(bot_id, enabled);

INSERT INTO bot_guilds (bot_id, guild_id, enabled)
VALUES
    ('irsia', '1520964851046944900', TRUE),
    ('irsia', '928619302213533736', TRUE)
ON CONFLICT (bot_id, guild_id) DO NOTHING;

INSERT INTO bot_voice_lines (bot_id, guild_id, join_line, revive_line, enabled)
VALUES
    ('irsia', '1520964851046944900', '', '', TRUE),
    ('irsia', '928619302213533736', '', '', TRUE)
ON CONFLICT (bot_id, guild_id) DO NOTHING;
