-- API attempts (not conversation text), retained with their request for seven days.
CREATE TABLE IF NOT EXISTS llm_api_attempts (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL REFERENCES llm_requests(message_id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    input_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS llm_api_attempts_window ON llm_api_attempts(model, attempted_at);
CREATE INDEX IF NOT EXISTS llm_api_attempts_message ON llm_api_attempts(message_id, id DESC);
-- Preserve already-used quota when migrating from aggregate attempt counters.
INSERT INTO llm_api_attempts(message_id, model, attempted_at, input_tokens)
SELECT r.message_id, r.model, r.last_attempt_at, greatest(r.input_tokens, 1)
FROM llm_requests r CROSS JOIN LATERAL generate_series(1, r.attempts) n
WHERE r.last_attempt_at > now() - interval '2 days'
  AND NOT EXISTS (SELECT 1 FROM llm_api_attempts a WHERE a.message_id=r.message_id);

CREATE TABLE IF NOT EXISTS llm_quota_blocks (
    model TEXT PRIMARY KEY,
    blocked_until TIMESTAMPTZ NOT NULL,
    reason TEXT NOT NULL,
    strikes INTEGER NOT NULL DEFAULT 1,
    observed_limits JSONB NOT NULL DEFAULT '{}'::jsonb
);
