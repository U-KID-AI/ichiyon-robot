ALTER TABLE mention_reaction_choices
    ADD COLUMN IF NOT EXISTS result_label TEXT;

ALTER TABLE mention_reaction_choices
    DROP CONSTRAINT IF EXISTS mention_reaction_choices_body_check;

ALTER TABLE mention_reaction_choices
    DROP CONSTRAINT IF EXISTS mention_reaction_choices_check;

ALTER TABLE reactions
    DROP CONSTRAINT IF EXISTS reactions_response_check;

ALTER TABLE reactions
    DROP CONSTRAINT IF EXISTS reactions_check;
