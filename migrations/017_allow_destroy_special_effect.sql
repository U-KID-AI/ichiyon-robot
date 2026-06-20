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
            'extra_choice',
            'destroy'
        )
    );
