-- Only metadata is durable. Conversation text remains in Discord.
CREATE TABLE IF NOT EXISTS llm_requests (
    message_id BIGINT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'claimed',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    reply_id BIGINT
);
CREATE INDEX IF NOT EXISTS llm_requests_created ON llm_requests(created_at);
