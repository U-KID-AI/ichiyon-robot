-- Correct the irsia mapping for いちよんラボ.
-- Migration 029 registered 1392174489609179327 as いちよんラボ by mistake.
-- That guild may be used as ランセ地方, so keep it in guilds and only remove irsia links.

INSERT INTO guilds (guild_id, name, enabled)
VALUES
    ('1515983621461245972', 'いちよんラボ', TRUE)
ON CONFLICT (guild_id) DO NOTHING;

INSERT INTO bot_guilds (bot_id, guild_id, enabled)
VALUES
    ('irsia', '1515983621461245972', TRUE)
ON CONFLICT (bot_id, guild_id) DO NOTHING;

INSERT INTO bot_voice_lines (bot_id, guild_id, join_line, revive_line, enabled)
VALUES
    ('irsia', '1515983621461245972', '', '', TRUE)
ON CONFLICT (bot_id, guild_id) DO NOTHING;

DELETE FROM bot_guilds
WHERE bot_id = 'irsia'
  AND guild_id = '1392174489609179327';

DELETE FROM bot_voice_lines
WHERE bot_id = 'irsia'
  AND guild_id = '1392174489609179327';
