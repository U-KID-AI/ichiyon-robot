ALTER TABLE special_effect_tags
    ADD COLUMN IF NOT EXISTS max_multiplier NUMERIC;

ALTER TABLE special_effect_tags
    ADD COLUMN IF NOT EXISTS multiplier_updated_by TEXT;
