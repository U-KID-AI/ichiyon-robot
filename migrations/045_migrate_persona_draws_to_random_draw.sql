-- Migrate persona_draws into generic random draw configuration.
-- This keeps legacy persona tables intact and only upserts random draw rows.

DO $$
DECLARE
    draw RECORD;
    reaction_id BIGINT;
    config JSONB;
BEGIN
    IF to_regclass('public.persona_draws') IS NULL THEN
        RETURN;
    END IF;

    FOR draw IN
        SELECT *
        FROM persona_draws
        ORDER BY id ASC
    LOOP
        SELECT id
        INTO reaction_id
        FROM mention_reactions
        WHERE bot_id = draw.bot_id
          AND guild_id = draw.guild_id
          AND (
              reaction_key = 'persona_draw_' || draw.id::TEXT
              OR keyword = draw.trigger_text
          )
        ORDER BY
            CASE WHEN reaction_key = 'persona_draw_' || draw.id::TEXT THEN 0 ELSE 1 END,
            id ASC
        LIMIT 1;

        SELECT jsonb_build_object(
            'allow_standalone_trigger', TRUE,
            'allow_mention_trigger', TRUE,
            'consume_mention', TRUE,
            'reroll_enabled', COALESCE(draw.reroll_enabled, FALSE),
            'reroll_probability_percent', COALESCE(draw.reroll_probability_percent, 0),
            'max_rerolls', COALESCE(draw.max_rerolls, 0),
            'reroll_lines', COALESCE(
                (
                    SELECT jsonb_agg(body ORDER BY sort_order ASC, id ASC)
                    FROM persona_draw_reroll_lines
                    WHERE bot_id = draw.bot_id
                      AND guild_id = draw.guild_id
                      AND persona_draw_id = draw.id
                      AND enabled = TRUE
                ),
                '[]'::JSONB
            ),
            'migrated_from', 'persona_draws',
            'persona_draw_id', draw.id
        )
        INTO config;

        IF reaction_id IS NULL THEN
            INSERT INTO mention_reactions (
                bot_id,
                guild_id,
                reaction_key,
                keyword,
                match_type,
                reaction_kind,
                name,
                description,
                admin_only,
                is_system,
                is_deletable,
                config_json,
                enabled,
                sort_order
            )
            VALUES (
                draw.bot_id,
                draw.guild_id,
                'persona_draw_' || draw.id::TEXT,
                draw.trigger_text,
                'exact',
                'random',
                draw.name,
                '旧ペルソナ抽選から移行したランダム抽選です。',
                FALSE,
                FALSE,
                TRUE,
                config,
                draw.enabled,
                0
            )
            RETURNING id INTO reaction_id;
        ELSE
            UPDATE mention_reactions
            SET keyword = draw.trigger_text,
                match_type = 'exact',
                reaction_kind = 'random',
                name = draw.name,
                description = COALESCE(NULLIF(description, ''), '旧ペルソナ抽選から移行したランダム抽選です。'),
                admin_only = FALSE,
                is_system = FALSE,
                is_deletable = TRUE,
                config_json = COALESCE(config_json, '{}'::JSONB) || config,
                enabled = draw.enabled,
                updated_at = NOW()
            WHERE id = reaction_id;
        END IF;

        INSERT INTO mention_reaction_choices (
            bot_id,
            guild_id,
            mention_reaction_id,
            name,
            body,
            image_path,
            appearance_rate,
            enabled,
            result_label,
            emoji_internal,
            sort_order
        )
        SELECT
            c.bot_id,
            c.guild_id,
            reaction_id,
            c.name,
            c.body,
            '',
            COALESCE(c.weight, 1),
            c.enabled,
            '',
            '',
            c.sort_order
        FROM persona_draw_candidates c
        WHERE c.bot_id = draw.bot_id
          AND c.guild_id = draw.guild_id
          AND c.persona_draw_id = draw.id
          AND NOT EXISTS (
              SELECT 1
              FROM mention_reaction_choices existing
              WHERE existing.bot_id = c.bot_id
                AND existing.guild_id = c.guild_id
                AND existing.mention_reaction_id = reaction_id
                AND COALESCE(existing.body, '') = COALESCE(c.body, '')
          );
    END LOOP;
END $$;
