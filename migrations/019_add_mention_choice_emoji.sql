ALTER TABLE mention_reaction_choices
    ADD COLUMN IF NOT EXISTS emoji_internal TEXT;
