DO $$
BEGIN
    IF to_regclass('public.modes') IS NOT NULL THEN
        ALTER TABLE modes
            ADD COLUMN IF NOT EXISTS admin_only BOOLEAN NOT NULL DEFAULT FALSE;

        ALTER TABLE modes
            ADD COLUMN IF NOT EXISTS is_deletable BOOLEAN NOT NULL DEFAULT TRUE;
    END IF;
END $$;
