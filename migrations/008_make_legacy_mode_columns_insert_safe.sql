DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'modes'
          AND column_name = 'display_name'
    ) THEN
        ALTER TABLE modes
            ALTER COLUMN display_name SET DEFAULT '';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'modes'
          AND column_name = 'activation_type'
    ) THEN
        ALTER TABLE modes
            ALTER COLUMN activation_type SET DEFAULT 'manual';
    END IF;
END $$;
