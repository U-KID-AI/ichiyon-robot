-- Additive only. Catalog IDs are permanent and scoped by asset kind.
CREATE TABLE IF NOT EXISTS minecraft_cosmetic_assets (
    kind TEXT NOT NULL CHECK (kind IN ('skin', 'accessory')),
    asset_id INTEGER NOT NULL CHECK (asset_id BETWEEN 1 AND 127),
    asset_key TEXT NOT NULL CHECK (asset_key ~ '^[a-z][a-z0-9_]{0,39}$'),
    name TEXT NOT NULL,
    model TEXT CHECK (model IN ('classic', 'slim')),
    slot TEXT CHECK (slot IN ('hat', 'face', 'neck', 'back')),
    texture BYTEA NOT NULL CHECK (octet_length(texture) <= 4194304),
    geometry BYTEA,
    icon BYTEA,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (kind, asset_id),
    UNIQUE (kind, asset_key),
    CHECK ((kind = 'skin' AND asset_id >= 5 AND model IS NOT NULL AND slot IS NULL AND geometry IS NULL AND icon IS NULL)
        OR (kind = 'accessory' AND model IS NULL AND slot IS NOT NULL AND geometry IS NOT NULL AND icon IS NOT NULL))
);

CREATE SEQUENCE IF NOT EXISTS minecraft_cosmetic_export_revision AS INTEGER;

CREATE TABLE IF NOT EXISTS minecraft_cosmetic_servers (
    bot_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    catalog_digest TEXT NOT NULL CHECK (catalog_digest ~ '^[0-9a-f]{64}$'),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bot_id, guild_id)
);

CREATE TABLE IF NOT EXISTS minecraft_cosmetic_placements (
    request_id UUID PRIMARY KEY,
    bot_id TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    skin_id INTEGER NOT NULL CHECK (skin_id BETWEEN 1 AND 127),
    catalog_digest TEXT NOT NULL CHECK (catalog_digest ~ '^[0-9a-f]{64}$'),
    minecraft_player TEXT NOT NULL CHECK (minecraft_player ~ '^[A-Za-z0-9_]{1,16}$'),
    created_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','claimed','succeeded','failed')),
    reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '60 seconds',
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS minecraft_cosmetic_placements_pending
    ON minecraft_cosmetic_placements (bot_id, guild_id, created_at) WHERE status = 'pending';
