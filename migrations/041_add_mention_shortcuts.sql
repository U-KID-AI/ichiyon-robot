CREATE TABLE IF NOT EXISTS mention_shortcuts (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    name TEXT NOT NULL,
    trigger_text TEXT NOT NULL,
    trigger_key TEXT NOT NULL,
    match_type TEXT NOT NULL DEFAULT 'exact',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT mention_shortcuts_match_type_check CHECK (match_type IN ('exact')),
    CONSTRAINT mention_shortcuts_scope_trigger_unique UNIQUE (bot_id, guild_id, trigger_key)
);

CREATE INDEX IF NOT EXISTS idx_mention_shortcuts_scope_enabled
    ON mention_shortcuts(bot_id, guild_id, enabled, trigger_key);

CREATE TABLE IF NOT EXISTS mention_shortcut_price_targets (
    id BIGSERIAL PRIMARY KEY,
    shortcut_id BIGINT NOT NULL REFERENCES mention_shortcuts(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_product_id TEXT NOT NULL,
    lookup_type TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    include_historical_low BOOLEAN NOT NULL DEFAULT TRUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT mention_shortcut_price_provider_check CHECK (provider IN ('steam', 'itad', 'ntprices')),
    CONSTRAINT mention_shortcut_price_sort_non_negative CHECK (sort_order >= 0),
    CONSTRAINT mention_shortcut_price_target_unique UNIQUE (shortcut_id, provider, provider_product_id, lookup_type)
);

CREATE INDEX IF NOT EXISTS idx_mention_shortcut_price_targets_shortcut
    ON mention_shortcut_price_targets(shortcut_id, enabled, sort_order, id);

CREATE TABLE IF NOT EXISTS mention_shortcut_audio_actions (
    id BIGSERIAL PRIMARY KEY,
    shortcut_id BIGINT NOT NULL REFERENCES mention_shortcuts(id) ON DELETE CASCADE,
    audio_asset_id BIGINT REFERENCES audio_assets(id) ON DELETE SET NULL,
    play_condition TEXT NOT NULL DEFAULT 'bot_in_vc',
    volume_override INTEGER,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT mention_shortcut_audio_condition_check CHECK (play_condition IN ('bot_in_vc')),
    CONSTRAINT mention_shortcut_audio_volume_range CHECK (volume_override IS NULL OR (volume_override >= 0 AND volume_override <= 100))
);

CREATE INDEX IF NOT EXISTS idx_mention_shortcut_audio_actions_shortcut
    ON mention_shortcut_audio_actions(shortcut_id, enabled, id);

CREATE OR REPLACE FUNCTION seed_mention_shortcut(
    p_bot_id TEXT,
    p_guild_id TEXT,
    p_name TEXT,
    p_trigger_text TEXT,
    p_trigger_key TEXT
)
RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    v_shortcut_id BIGINT;
BEGIN
    INSERT INTO mention_shortcuts (
        bot_id, guild_id, name, trigger_text, trigger_key, match_type, enabled
    )
    VALUES (
        p_bot_id, p_guild_id, p_name, p_trigger_text, p_trigger_key, 'exact', TRUE
    )
    ON CONFLICT (bot_id, guild_id, trigger_key) DO UPDATE
    SET name = EXCLUDED.name,
        trigger_text = EXCLUDED.trigger_text,
        match_type = 'exact',
        updated_at = NOW()
    RETURNING id INTO v_shortcut_id;

    RETURN v_shortcut_id;
END;
$$;

DO $$
DECLARE
    v_guild RECORD;
    v_shortcut_id BIGINT;
BEGIN
    FOR v_guild IN
        SELECT bot_id, guild_id
        FROM bot_guilds
        WHERE bot_id = 'ichiyon' AND enabled = TRUE
    LOOP
        v_shortcut_id := seed_mention_shortcut(
            v_guild.bot_id,
            v_guild.guild_id,
            'ニコロデオン',
            'ニコロデオン',
            lower('ニコロデオン')
        );

        INSERT INTO mention_shortcut_price_targets (
            shortcut_id, provider, provider_product_id, lookup_type, display_name,
            sort_order, include_historical_low, enabled
        )
        VALUES
            (v_shortcut_id, 'steam', '1414850', 'app_id', 'Steam', 10, FALSE, TRUE),
            (v_shortcut_id, 'itad', '1414850', 'steam_app_id', 'PC過去最安(ITAD)', 20, TRUE, TRUE)
        ON CONFLICT DO NOTHING;
    END LOOP;
END;
$$;

DROP FUNCTION IF EXISTS seed_mention_shortcut(TEXT, TEXT, TEXT, TEXT, TEXT);
