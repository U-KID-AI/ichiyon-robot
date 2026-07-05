ALTER TABLE IF EXISTS x_update_watches
    ADD COLUMN IF NOT EXISTS include_keywords TEXT;

ALTER TABLE IF EXISTS x_update_watches
    ADD COLUMN IF NOT EXISTS exclude_keywords TEXT;
