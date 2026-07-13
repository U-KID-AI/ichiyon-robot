CREATE TABLE IF NOT EXISTS bot_music_settings (
    bot_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    music_volume_percent INTEGER NOT NULL DEFAULT 40,
    foreground_volume_percent INTEGER NOT NULL DEFAULT 50,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bot_id, guild_id),
    CHECK (music_volume_percent >= 0 AND music_volume_percent <= 100),
    CHECK (foreground_volume_percent >= 0 AND foreground_volume_percent <= 100)
);
