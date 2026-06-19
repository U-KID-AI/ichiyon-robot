DO $$
BEGIN
    IF to_regclass('public.mention_reactions') IS NOT NULL THEN
        ALTER TABLE mention_reactions
            DROP CONSTRAINT IF EXISTS mention_reactions_match_type_check;

        ALTER TABLE mention_reactions
            ADD CONSTRAINT mention_reactions_match_type_check
            CHECK (match_type IN ('contains', 'exact', 'prefix', 'regex'));
    END IF;
END $$;
