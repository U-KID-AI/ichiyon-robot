CREATE TABLE IF NOT EXISTS youtube_n_pull_presets (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL DEFAULT 'ichiyon',
    guild_id TEXT NOT NULL REFERENCES guilds(guild_id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    command_name TEXT NOT NULL,
    command_key TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    max_pulls INTEGER NOT NULL DEFAULT 100 CHECK (max_pulls BETWEEN 1 AND 100),
    cache_ttl_seconds INTEGER NOT NULL DEFAULT 86400 CHECK (cache_ttl_seconds >= 60),
    include_shorts BOOLEAN NOT NULL DEFAULT FALSE,
    include_live BOOLEAN NOT NULL DEFAULT FALSE,
    include_archived_live BOOLEAN NOT NULL DEFAULT FALSE,
    min_duration_seconds INTEGER,
    max_duration_seconds INTEGER,
    include_title_terms TEXT NOT NULL DEFAULT '',
    exclude_title_terms TEXT NOT NULL DEFAULT '',
    last_cache_refresh_at TIMESTAMPTZ,
    last_cache_error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (min_duration_seconds IS NULL OR min_duration_seconds >= 0),
    CHECK (max_duration_seconds IS NULL OR max_duration_seconds >= 0),
    CHECK (
        min_duration_seconds IS NULL
        OR max_duration_seconds IS NULL
        OR min_duration_seconds <= max_duration_seconds
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_youtube_n_pull_presets_scope_command
    ON youtube_n_pull_presets(bot_id, guild_id, command_key);

CREATE INDEX IF NOT EXISTS idx_youtube_n_pull_presets_scope
    ON youtube_n_pull_presets(bot_id, guild_id);

CREATE TABLE IF NOT EXISTS youtube_n_pull_sources (
    id BIGSERIAL PRIMARY KEY,
    preset_id BIGINT NOT NULL REFERENCES youtube_n_pull_presets(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK (source_type IN ('channel', 'playlist')),
    source_url TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(preset_id, source_url)
);

CREATE INDEX IF NOT EXISTS idx_youtube_n_pull_sources_preset
    ON youtube_n_pull_sources(preset_id, enabled, priority, id);

CREATE TABLE IF NOT EXISTS youtube_n_pull_cache_videos (
    id BIGSERIAL PRIMARY KEY,
    preset_id BIGINT NOT NULL REFERENCES youtube_n_pull_presets(id) ON DELETE CASCADE,
    source_id BIGINT REFERENCES youtube_n_pull_sources(id) ON DELETE SET NULL,
    video_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    duration_seconds INTEGER,
    live_status TEXT NOT NULL DEFAULT '',
    published_at TIMESTAMPTZ,
    cached_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
    UNIQUE(preset_id, video_id)
);

CREATE INDEX IF NOT EXISTS idx_youtube_n_pull_cache_preset
    ON youtube_n_pull_cache_videos(preset_id, cached_at DESC);

CREATE OR REPLACE FUNCTION seed_youtube_n_pull_preset(
    p_bot_id TEXT,
    p_guild_id TEXT,
    p_display_name TEXT,
    p_command_name TEXT,
    p_command_key TEXT,
    p_aliases TEXT,
    p_category TEXT,
    p_enabled BOOLEAN,
    p_max_pulls INTEGER,
    p_cache_ttl_seconds INTEGER,
    p_include_shorts BOOLEAN,
    p_include_live BOOLEAN,
    p_include_archived_live BOOLEAN,
    p_min_duration_seconds INTEGER,
    p_max_duration_seconds INTEGER,
    p_include_title_terms TEXT,
    p_exclude_title_terms TEXT
) RETURNS BIGINT AS $$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO youtube_n_pull_presets (
        bot_id, guild_id, display_name, command_name, command_key, aliases, category,
        enabled, max_pulls, cache_ttl_seconds, include_shorts, include_live,
        include_archived_live, min_duration_seconds, max_duration_seconds,
        include_title_terms, exclude_title_terms
    )
    VALUES (
        p_bot_id, p_guild_id, p_display_name, p_command_name, p_command_key, p_aliases, p_category,
        p_enabled, p_max_pulls, p_cache_ttl_seconds, p_include_shorts, p_include_live,
        p_include_archived_live, p_min_duration_seconds, p_max_duration_seconds,
        p_include_title_terms, p_exclude_title_terms
    )
    ON CONFLICT (bot_id, guild_id, command_key) DO UPDATE
    SET display_name = EXCLUDED.display_name,
        command_name = EXCLUDED.command_name,
        aliases = EXCLUDED.aliases,
        category = EXCLUDED.category,
        enabled = EXCLUDED.enabled,
        max_pulls = EXCLUDED.max_pulls,
        cache_ttl_seconds = EXCLUDED.cache_ttl_seconds,
        include_shorts = EXCLUDED.include_shorts,
        include_live = EXCLUDED.include_live,
        include_archived_live = EXCLUDED.include_archived_live,
        min_duration_seconds = EXCLUDED.min_duration_seconds,
        max_duration_seconds = EXCLUDED.max_duration_seconds,
        include_title_terms = EXCLUDED.include_title_terms,
        exclude_title_terms = EXCLUDED.exclude_title_terms,
        updated_at = NOW()
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    v_guild RECORD;
    v_preset_id BIGINT;
BEGIN
    FOR v_guild IN
        SELECT bot_id, guild_id
        FROM bot_guilds
        WHERE enabled = TRUE
    LOOP
        v_preset_id := seed_youtube_n_pull_preset(
            v_guild.bot_id,
            v_guild.guild_id,
            '油粘土マン',
            '油粘土マン',
            '油粘土マン',
            '油粘土' || E'\n' || 'ねんどマン',
            'ネタ',
            FALSE,
            100,
            86400,
            FALSE,
            FALSE,
            FALSE,
            NULL,
            NULL,
            '',
            ''
        );

        v_preset_id := seed_youtube_n_pull_preset(
            v_guild.bot_id,
            v_guild.guild_id,
            'しゃろう',
            'しゃろう',
            'しゃろう',
            'シャロウ' || E'\n' || 'Sharou' || E'\n' || 'sharou',
            'BGM',
            TRUE,
            100,
            86400,
            FALSE,
            FALSE,
            FALSE,
            NULL,
            7200,
            '',
            ''
        );
        INSERT INTO youtube_n_pull_sources (preset_id, source_type, source_url, priority, enabled)
        VALUES (v_preset_id, 'channel', 'https://www.youtube.com/channel/UCfjca6Z_wpyinTqHdIYJ49Q', 100, TRUE)
        ON CONFLICT (preset_id, source_url) DO UPDATE
        SET source_type = EXCLUDED.source_type,
            priority = EXCLUDED.priority,
            enabled = TRUE,
            updated_at = NOW();

        v_preset_id := seed_youtube_n_pull_preset(
            v_guild.bot_id,
            v_guild.guild_id,
            'ペルソナ5',
            'ペルソナ5',
            'ペルソナ5',
            'P5' || E'\n' || 'p5' || E'\n' || 'Persona5' || E'\n' || 'Persona 5' || E'\n' || 'ペルソナ５',
            'ゲーム音楽',
            FALSE,
            100,
            86400,
            FALSE,
            FALSE,
            FALSE,
            NULL,
            NULL,
            '',
            'cover' || E'\n' || '歌ってみた' || E'\n' || 'remix' || E'\n' || 'live' || E'\n' || 'extended' || E'\n' || '1 hour' || E'\n' || '耐久' || E'\n' || 'chiptune' || E'\n' || 'karaoke'
        );
    END LOOP;
END;
$$;

DROP FUNCTION IF EXISTS seed_youtube_n_pull_preset(
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN, INTEGER, INTEGER, BOOLEAN, BOOLEAN,
    BOOLEAN, INTEGER, INTEGER, TEXT, TEXT
);
