-- Additive catalog extension: never delete or renumber existing assets.
ALTER TABLE minecraft_cosmetic_assets
    ADD COLUMN IF NOT EXISTS width INTEGER,
    ADD COLUMN IF NOT EXISTS height INTEGER;

ALTER TABLE minecraft_cosmetic_assets
    DROP CONSTRAINT IF EXISTS minecraft_cosmetic_assets_kind_check,
    DROP CONSTRAINT IF EXISTS minecraft_cosmetic_assets_check,
    DROP CONSTRAINT IF EXISTS minecraft_cosmetic_assets_shape_check;

ALTER TABLE minecraft_cosmetic_assets
    ADD CONSTRAINT minecraft_cosmetic_assets_kind_check
        CHECK (kind IN ('skin', 'accessory', 'poster')),
    ADD CONSTRAINT minecraft_cosmetic_assets_shape_check CHECK (
        (kind = 'skin' AND asset_id >= 5 AND model IS NOT NULL AND slot IS NULL
            AND geometry IS NULL AND icon IS NULL AND width IS NULL AND height IS NULL)
        OR (kind = 'accessory' AND model IS NULL AND slot IS NOT NULL
            AND geometry IS NOT NULL AND icon IS NOT NULL AND width IS NULL AND height IS NULL)
        OR (kind = 'poster' AND model IS NULL AND slot IS NULL AND geometry IS NULL AND icon IS NULL
            AND width IS NOT NULL AND height IS NOT NULL
            AND width BETWEEN 1 AND 10 AND height BETWEEN 1 AND 10)
    );

ALTER TABLE minecraft_cosmetic_deleted_assets
    DROP CONSTRAINT IF EXISTS minecraft_cosmetic_deleted_assets_kind_check;
ALTER TABLE minecraft_cosmetic_deleted_assets
    ADD CONSTRAINT minecraft_cosmetic_deleted_assets_kind_check
        CHECK (kind IN ('skin', 'accessory', 'poster'));
