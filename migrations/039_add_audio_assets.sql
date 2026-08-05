ALTER TABLE special_effect_tags
    DROP CONSTRAINT IF EXISTS special_effect_tags_effect_type_check;

ALTER TABLE special_effect_tags
    ADD CONSTRAINT special_effect_tags_effect_type_check
    CHECK (
        effect_type IN (
            'probability_multiplier',
            'next_action_count_add',
            'count_add',
            'mode_lottery',
            'pseudo_offline_lottery',
            'hankaku',
            'shikocchi_lottery',
            'custom',
            'probability_message',
            'message',
            'reaction',
            'audio_asset',
            'counter_delta',
            'counter_set',
            'next_action_count',
            'mode_roll',
            'mode_enter',
            'temporary_state',
            'ng_behavior',
            'extra_choice',
            'destroy',
            'mention_suffix_guard'
        )
    );

CREATE TABLE IF NOT EXISTS audio_assets (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    storage_path TEXT NOT NULL,
    original_filename TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER,
    default_volume INTEGER NOT NULL DEFAULT 50,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT audio_assets_default_volume_range CHECK (default_volume >= 0 AND default_volume <= 100),
    CONSTRAINT audio_assets_duration_non_negative CHECK (duration_ms IS NULL OR duration_ms >= 0),
    CONSTRAINT audio_assets_scope_storage_unique UNIQUE (bot_id, guild_id, storage_path)
);

CREATE INDEX IF NOT EXISTS idx_audio_assets_scope_enabled
    ON audio_assets (bot_id, guild_id, enabled, category, display_name);
