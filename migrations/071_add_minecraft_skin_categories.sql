-- Category metadata is independent of immutable skin IDs and image blobs.
CREATE TABLE IF NOT EXISTS minecraft_skin_categories (
    category_id TEXT PRIMARY KEY CHECK (category_id ~ '^[a-z][a-z0-9_]{0,39}$'),
    name TEXT NOT NULL UNIQUE CHECK (char_length(name) BETWEEN 1 AND 48
        AND name = btrim(name) AND name !~ '[[:cntrl:]§]'
        AND name <> 'すべてのキャラクター'),
    CHECK (category_id <> 'uncategorized' OR name = 'その他・未分類')
);
INSERT INTO minecraft_skin_categories (category_id, name) VALUES
    ('uncategorized', 'その他・未分類'), ('honkai3rd', '崩壊3rd'), ('arknights', 'アークナイツ')
ON CONFLICT DO NOTHING;

-- Also holds the four built-ins whose images intentionally live in the pack.
CREATE TABLE IF NOT EXISTS minecraft_skin_category_assignments (
    skin_id INTEGER PRIMARY KEY CHECK (skin_id BETWEEN 1 AND 127),
    category_id TEXT NOT NULL REFERENCES minecraft_skin_categories(category_id),
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO minecraft_skin_category_assignments (skin_id, category_id, updated_by) VALUES
    (1, 'honkai3rd', 'migration:071'), (2, 'honkai3rd', 'migration:071'),
    (3, 'honkai3rd', 'migration:071'), (4, 'uncategorized', 'migration:071')
ON CONFLICT (skin_id) DO NOTHING;
INSERT INTO minecraft_skin_category_assignments (skin_id, category_id, updated_by)
SELECT asset_id, 'uncategorized', 'migration:071' FROM minecraft_cosmetic_assets WHERE kind = 'skin'
ON CONFLICT (skin_id) DO NOTHING;
