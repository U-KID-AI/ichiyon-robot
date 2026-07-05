-- Fix bot/guild ownership for irsia rollout.
-- Additive only: no existing rows are changed or deleted by this migration.

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

-- Preserve existing ichiyon visibility, but do not attach irsia-only guilds to ichiyon.
INSERT INTO bot_guilds (bot_id, guild_id, enabled)
SELECT 'ichiyon', g.guild_id, TRUE
FROM guilds g
WHERE g.guild_id NOT IN ('1520964851046944900', '928619302213533736')
ON CONFLICT (bot_id, guild_id) DO NOTHING;

INSERT INTO guilds (guild_id, name, enabled)
VALUES
    ('1392174489609179327', 'いちよんラボ', TRUE),
    ('1520964851046944900', '天使の聖域', TRUE),
    ('928619302213533736', '神聖イルシア皇国', TRUE)
ON CONFLICT (guild_id) DO NOTHING;

INSERT INTO bot_guilds (bot_id, guild_id, enabled)
VALUES
    ('irsia', '1392174489609179327', TRUE),
    ('irsia', '1520964851046944900', TRUE),
    ('irsia', '928619302213533736', TRUE)
ON CONFLICT (bot_id, guild_id) DO NOTHING;

INSERT INTO bot_voice_lines (bot_id, guild_id, join_line, revive_line, enabled)
VALUES
    ('irsia', '1392174489609179327', '', '', TRUE),
    ('irsia', '1520964851046944900', '', '', TRUE),
    ('irsia', '928619302213533736', '', '', TRUE)
ON CONFLICT (bot_id, guild_id) DO NOTHING;
