CREATE TABLE IF NOT EXISTS minecraft_player_links (
    bot_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    discord_user_id TEXT NOT NULL,
    minecraft_player_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bot_id, guild_id, discord_user_id),
    CONSTRAINT minecraft_player_links_name_safe
        CHECK (minecraft_player_name ~ '^[A-Za-z0-9_]{1,16}$')
);

CREATE TABLE IF NOT EXISTS minecraft_command_queue (
    id BIGSERIAL PRIMARY KEY,
    request_id UUID NOT NULL UNIQUE,
    bot_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    discord_channel_id TEXT NOT NULL,
    discord_message_id TEXT NOT NULL,
    requester_discord_user_id TEXT NOT NULL,
    target_discord_user_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    minecraft_player_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    claim_token TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '60 seconds'),
    result_reason TEXT,
    result_message TEXT,
    CONSTRAINT minecraft_command_queue_type_safe
        CHECK (command_type IN ('narita_carpet')),
    CONSTRAINT minecraft_command_queue_status_safe
        CHECK (status IN ('pending', 'claimed', 'succeeded', 'failed')),
    CONSTRAINT minecraft_command_queue_player_name_safe
        CHECK (minecraft_player_name ~ '^[A-Za-z0-9_]{1,16}$')
);

CREATE INDEX IF NOT EXISTS minecraft_command_queue_pending_idx
    ON minecraft_command_queue (bot_id, guild_id, status, created_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS minecraft_command_queue_request_status_idx
    ON minecraft_command_queue (request_id, status);
