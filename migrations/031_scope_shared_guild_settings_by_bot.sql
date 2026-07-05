-- Scope shared guild settings by bot_id.
-- This migration does not delete table data. It replaces old guild-only uniqueness
-- with bot_id + guild_id uniqueness so the same Discord guild can be configured
-- independently for ichiyon and irsia.

ALTER TABLE IF EXISTS feature_flags
    DROP CONSTRAINT IF EXISTS feature_flags_guild_id_feature_key_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_feature_flags_bot_guild_feature_unique
    ON feature_flags(bot_id, guild_id, feature_key);

ALTER TABLE IF EXISTS mention_reactions
    DROP CONSTRAINT IF EXISTS mention_reactions_guild_id_reaction_key_key;
ALTER TABLE IF EXISTS mention_reactions
    DROP CONSTRAINT IF EXISTS mention_reactions_guild_id_keyword_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mention_reactions_bot_guild_reaction_key_unique
    ON mention_reactions(bot_id, guild_id, reaction_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mention_reactions_bot_guild_keyword_unique
    ON mention_reactions(bot_id, guild_id, keyword);

ALTER TABLE IF EXISTS mention_search_handlers
    DROP CONSTRAINT IF EXISTS mention_search_handlers_guild_id_handler_key_key;
ALTER TABLE IF EXISTS mention_search_handlers
    DROP CONSTRAINT IF EXISTS mention_search_handlers_mention_reaction_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mention_search_handlers_bot_guild_handler_key_unique
    ON mention_search_handlers(bot_id, guild_id, handler_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mention_search_handlers_bot_reaction_unique
    ON mention_search_handlers(bot_id, mention_reaction_id);

ALTER TABLE IF EXISTS ng_words
    DROP CONSTRAINT IF EXISTS ng_words_guild_id_word_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_ng_words_bot_guild_word_unique
    ON ng_words(bot_id, guild_id, word);

ALTER TABLE IF EXISTS special_effect_tags
    DROP CONSTRAINT IF EXISTS special_effect_tags_guild_id_name_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_special_effect_tags_bot_guild_name_unique
    ON special_effect_tags(bot_id, guild_id, name);

ALTER TABLE IF EXISTS special_effect_assignments
    DROP CONSTRAINT IF EXISTS special_effect_assignments_special_effect_tag_id_target_type_target_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_special_effect_assignments_bot_tag_target_unique
    ON special_effect_assignments(bot_id, special_effect_tag_id, target_type, target_id);

ALTER TABLE IF EXISTS counters
    DROP CONSTRAINT IF EXISTS counters_guild_id_count_key_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_counters_bot_guild_count_key_unique
    ON counters(bot_id, guild_id, count_key);

ALTER TABLE IF EXISTS counter_states
    DROP CONSTRAINT IF EXISTS counter_states_guild_id_counter_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_counter_states_bot_guild_counter_unique
    ON counter_states(bot_id, guild_id, counter_id);

ALTER TABLE IF EXISTS modes
    DROP CONSTRAINT IF EXISTS modes_guild_id_mode_key_key;
DROP INDEX IF EXISTS idx_modes_guild_mode_key_unique;
CREATE UNIQUE INDEX IF NOT EXISTS idx_modes_bot_guild_mode_key_unique
    ON modes(bot_id, guild_id, mode_key);

ALTER TABLE IF EXISTS mode_states
    DROP CONSTRAINT IF EXISTS mode_states_pkey;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mode_states_bot_guild_unique
    ON mode_states(bot_id, guild_id);

ALTER TABLE IF EXISTS mode_trigger_history
    DROP CONSTRAINT IF EXISTS mode_trigger_history_guild_id_mode_id_period_key_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mode_trigger_history_bot_guild_mode_period_unique
    ON mode_trigger_history(bot_id, guild_id, mode_id, period_key);

ALTER TABLE IF EXISTS mention_limited_effects
    DROP CONSTRAINT IF EXISTS mention_limited_effects_guild_id_discord_user_id_effect_tag_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mention_limited_effects_bot_guild_user_tag_unique
    ON mention_limited_effects(bot_id, guild_id, discord_user_id, effect_tag_id);

ALTER TABLE IF EXISTS reaction_threshold_events
    DROP CONSTRAINT IF EXISTS reaction_threshold_events_guild_id_rule_id_message_id_emoji_key_threshold_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_reaction_threshold_events_bot_guild_rule_message_emoji_unique
    ON reaction_threshold_events(bot_id, guild_id, rule_id, message_id, emoji_key, threshold);
