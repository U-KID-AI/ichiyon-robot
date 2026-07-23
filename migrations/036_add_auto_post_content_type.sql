ALTER TABLE IF EXISTS auto_posts
    ADD COLUMN IF NOT EXISTS content_type TEXT NOT NULL DEFAULT 'static';

ALTER TABLE IF EXISTS auto_posts
    ADD COLUMN IF NOT EXISTS content_config_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_auto_posts_bot_guild_content_type
    ON auto_posts(bot_id, guild_id, content_type);
