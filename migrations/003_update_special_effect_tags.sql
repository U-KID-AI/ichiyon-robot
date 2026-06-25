DO $$
BEGIN
    IF to_regclass('public.special_effect_tags') IS NOT NULL THEN
        ALTER TABLE special_effect_tags
            ADD COLUMN IF NOT EXISTS target_type TEXT NOT NULL DEFAULT 'mention_reaction_choice';

        ALTER TABLE special_effect_tags
            ADD COLUMN IF NOT EXISTS trigger_timing TEXT NOT NULL DEFAULT 'choice_selected';

        ALTER TABLE special_effect_tags
            ADD COLUMN IF NOT EXISTS expires_type TEXT NOT NULL DEFAULT 'permanent';

        ALTER TABLE special_effect_tags
            ADD COLUMN IF NOT EXISTS expires_value INTEGER;

        ALTER TABLE special_effect_tags
            ADD COLUMN IF NOT EXISTS cooldown_scope TEXT NOT NULL DEFAULT 'none';

        ALTER TABLE special_effect_tags
            ADD COLUMN IF NOT EXISTS additional_text TEXT;

        ALTER TABLE special_effect_tags
            ADD COLUMN IF NOT EXISTS additional_post_timing TEXT NOT NULL DEFAULT 'none';

        ALTER TABLE special_effect_tags
            ADD COLUMN IF NOT EXISTS cooldown_seconds INTEGER;

        ALTER TABLE special_effect_tags
            ADD COLUMN IF NOT EXISTS effect_config_json JSONB NOT NULL DEFAULT '{}'::JSONB;

        ALTER TABLE special_effect_tags
            DROP CONSTRAINT IF EXISTS special_effect_tags_target_type_check;
        ALTER TABLE special_effect_tags
            ADD CONSTRAINT special_effect_tags_target_type_check
            CHECK (target_type IN ('mention_reaction_choice', 'auto_reaction', 'ng_word'));

        ALTER TABLE special_effect_tags
            DROP CONSTRAINT IF EXISTS special_effect_tags_trigger_timing_check;
        ALTER TABLE special_effect_tags
            ADD CONSTRAINT special_effect_tags_trigger_timing_check
            CHECK (trigger_timing IN ('choice_selected', 'auto_reaction_triggered', 'ng_word_detected'));

        ALTER TABLE special_effect_tags
            DROP CONSTRAINT IF EXISTS special_effect_tags_effect_type_check;
        ALTER TABLE special_effect_tags
            ADD CONSTRAINT special_effect_tags_effect_type_check
            CHECK (
                effect_type IN (
                    'probability_multiplier',
                    'next_action_count_add',
                    'count_add',
                    'mode_lottery',
                    'pseudo_offline_lottery',
                    'hankaku',
                    'shikocchi_lottery',
                    'custom',
                    'probability_message',
                    'message',
                    'reaction',
                    'counter_delta',
                    'counter_set',
                    'next_action_count',
                    'mode_roll',
                    'mode_enter',
                    'temporary_state',
                    'ng_behavior',
                    'extra_choice'
                )
            );

        ALTER TABLE special_effect_tags
            DROP CONSTRAINT IF EXISTS special_effect_tags_additional_post_timing_check;
        ALTER TABLE special_effect_tags
            ADD CONSTRAINT special_effect_tags_additional_post_timing_check
            CHECK (additional_post_timing IN ('none', 'before_response', 'after_response', 'tag_triggered', 'effect_success', 'effect_end'));

        ALTER TABLE special_effect_tags
            DROP CONSTRAINT IF EXISTS special_effect_tags_expires_type_check;
        ALTER TABLE special_effect_tags
            ADD CONSTRAINT special_effect_tags_expires_type_check
            CHECK (expires_type IN ('immediate', 'next_bot_action', 'next_special_roll', 'seconds', 'count', 'permanent'));

        ALTER TABLE special_effect_tags
            DROP CONSTRAINT IF EXISTS special_effect_tags_expires_value_check;
        ALTER TABLE special_effect_tags
            ADD CONSTRAINT special_effect_tags_expires_value_check
            CHECK (expires_value IS NULL OR expires_value >= 0);

        ALTER TABLE special_effect_tags
            DROP CONSTRAINT IF EXISTS special_effect_tags_cooldown_scope_check;
        ALTER TABLE special_effect_tags
            ADD CONSTRAINT special_effect_tags_cooldown_scope_check
            CHECK (cooldown_scope IN ('none', 'guild', 'channel', 'user', 'assigned_event'));
    END IF;

    IF to_regclass('public.special_effect_assignments') IS NOT NULL THEN
        ALTER TABLE special_effect_assignments
            DROP CONSTRAINT IF EXISTS special_effect_assignments_target_type_check;
        ALTER TABLE special_effect_assignments
            ADD CONSTRAINT special_effect_assignments_target_type_check
            CHECK (target_type IN ('mention_reaction_choice', 'reaction', 'auto_reaction', 'ng_word'));
    END IF;
END $$;
