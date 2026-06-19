DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    SELECT c.conname
    INTO constraint_name
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE t.relname = 'mode_exit_conditions'
      AND n.nspname = 'public'
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) LIKE '%duration_elapsed%'
      AND pg_get_constraintdef(c.oid) LIKE '%manual%'
    LIMIT 1;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE mode_exit_conditions DROP CONSTRAINT %I', constraint_name);
    END IF;

    ALTER TABLE mode_exit_conditions
        ADD CONSTRAINT mode_exit_conditions_condition_type_check
        CHECK (condition_type IN ('duration', 'duration_elapsed', 'manual'));
END $$;
