DO $$
BEGIN
    IF to_regclass('public.reactions') IS NOT NULL THEN
        ALTER TABLE reactions
            DROP CONSTRAINT IF EXISTS reactions_match_type_check;

        ALTER TABLE reactions
            ADD CONSTRAINT reactions_match_type_check
            CHECK (match_type IN ('contains', 'exact', 'prefix', 'regex'));
    END IF;
END $$;
