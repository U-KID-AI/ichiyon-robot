INSERT INTO mention_shortcut_price_targets (
    shortcut_id, provider, provider_product_id, lookup_type, display_name,
    sort_order, include_historical_low, enabled
)
SELECT
    s.id,
    'ntprices',
    '70010000057297',
    'nsuid',
    'Nintendo Switch',
    30,
    FALSE,
    TRUE
FROM mention_shortcuts s
WHERE s.bot_id = 'ichiyon'
  AND s.trigger_key = lower('ニコロデオン')
  AND s.enabled = TRUE
ON CONFLICT (shortcut_id, provider, provider_product_id, lookup_type) DO UPDATE
SET display_name = EXCLUDED.display_name,
    sort_order = EXCLUDED.sort_order,
    include_historical_low = EXCLUDED.include_historical_low,
    enabled = EXCLUDED.enabled,
    updated_at = NOW();
