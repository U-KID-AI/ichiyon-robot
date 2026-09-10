-- Decouple user-management permission from bot/guild settings permissions
-- and allow the generic fixed-user probability message special effect.

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
            'probability_user_message',
            'message',
            'reaction',
            'audio_asset',
            'counter_delta',
            'counter_set',
            'next_action_count',
            'mode_roll',
            'mode_enter',
            'temporary_state',
            'ng_behavior',
            'extra_choice',
            'destroy',
            'mention_suffix_guard'
        )
    );

INSERT INTO bot_permissions (bot_id, discord_user_id, guild_id, role)
SELECT
    b.bot_id,
    au.discord_user_id,
    NULL,
    'global_admin'
FROM admin_users au
CROSS JOIN bot_instances b
WHERE au.enabled = TRUE
  AND au.role = 'global_admin'
  AND b.enabled = TRUE
  AND NOT EXISTS (
      SELECT 1
      FROM bot_permissions bp
      WHERE bp.bot_id = b.bot_id
        AND bp.discord_user_id = au.discord_user_id
        AND bp.guild_id IS NULL
  );

UPDATE admin_users
SET can_manage_users = (role = 'global_admin'),
    updated_at = NOW()
WHERE can_manage_users IS DISTINCT FROM (role = 'global_admin');
