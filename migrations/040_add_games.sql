CREATE TABLE IF NOT EXISTS games (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_game_id TEXT NOT NULL,
    title TEXT NOT NULL,
    store_url TEXT NOT NULL DEFAULT '',
    release_date TEXT NOT NULL DEFAULT '',
    platforms JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_known_price INTEGER,
    last_known_regular_price INTEGER,
    last_known_discount_percent INTEGER,
    currency TEXT NOT NULL DEFAULT '',
    historical_low INTEGER,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    fetched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT games_provider_game_unique UNIQUE (provider, provider_game_id),
    CONSTRAINT games_price_non_negative CHECK (last_known_price IS NULL OR last_known_price >= 0),
    CONSTRAINT games_regular_price_non_negative CHECK (last_known_regular_price IS NULL OR last_known_regular_price >= 0),
    CONSTRAINT games_discount_range CHECK (last_known_discount_percent IS NULL OR (last_known_discount_percent >= 0 AND last_known_discount_percent <= 100)),
    CONSTRAINT games_historical_low_non_negative CHECK (historical_low IS NULL OR historical_low >= 0)
);

CREATE INDEX IF NOT EXISTS idx_games_title
    ON games (title);

CREATE TABLE IF NOT EXISTS user_game_entries (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    discord_user_id TEXT NOT NULL,
    game_id BIGINT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    owned BOOLEAN NOT NULL DEFAULT FALSE,
    wishlist BOOLEAN NOT NULL DEFAULT FALSE,
    backlog BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT user_game_entries_scope_unique UNIQUE (bot_id, guild_id, discord_user_id, game_id)
);

CREATE INDEX IF NOT EXISTS idx_user_game_entries_user_scope
    ON user_game_entries (bot_id, guild_id, discord_user_id);

CREATE TABLE IF NOT EXISTS game_search_history (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    discord_user_id TEXT NOT NULL,
    query TEXT NOT NULL,
    game_id BIGINT REFERENCES games(id) ON DELETE SET NULL,
    searched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_game_search_history_scope_recent
    ON game_search_history (bot_id, guild_id, discord_user_id, searched_at DESC);
