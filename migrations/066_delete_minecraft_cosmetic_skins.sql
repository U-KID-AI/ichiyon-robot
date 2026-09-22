-- Logical skin deletion. Deleted IDs are permanent gaps and are never recycled.
ALTER TABLE minecraft_cosmetic_assets
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS minecraft_cosmetic_deleted_assets (
    kind TEXT NOT NULL CHECK (kind IN ('skin', 'accessory')),
    asset_id INTEGER NOT NULL CHECK (asset_id BETWEEN 1 AND 127),
    deleted_by TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (kind, asset_id)
);

CREATE INDEX IF NOT EXISTS minecraft_cosmetic_assets_active
    ON minecraft_cosmetic_assets (kind, asset_id)
    WHERE deleted_at IS NULL;

DROP TABLE IF EXISTS minecraft_cosmetic_placements;
