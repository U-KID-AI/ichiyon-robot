CREATE TABLE IF NOT EXISTS random_reaction_settings (
    bot_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    emoji TEXT NOT NULL DEFAULT '🍞',
    probability_percent NUMERIC(6, 3) NOT NULL DEFAULT 1.000,
    cooldown_seconds INTEGER NOT NULL DEFAULT 600,
    target_channel_ids TEXT NOT NULL DEFAULT '',
    excluded_channel_ids TEXT NOT NULL DEFAULT '',
    updated_by_discord_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bot_id, guild_id),
    CHECK (probability_percent >= 0 AND probability_percent <= 100),
    CHECK (cooldown_seconds >= 0)
);
