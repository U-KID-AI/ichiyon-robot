CREATE TABLE IF NOT EXISTS ai_tasks (
    task_id UUID PRIMARY KEY,
    bot_id TEXT NOT NULL,
    guild_id TEXT,
    discord_channel_id TEXT NOT NULL,
    discord_message_id TEXT NOT NULL,
    requester_discord_user_id TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    branch_name VARCHAR(128) NOT NULL,
    worktree_name VARCHAR(128) NOT NULL,
    current_step TEXT,
    progress_summary TEXT,
    result_summary TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    CONSTRAINT ai_tasks_description_length
        CHECK (char_length(description) BETWEEN 1 AND 4000),
    CONSTRAINT ai_tasks_status_safe
        CHECK (status IN (
            'queued',
            'running',
            'testing',
            'needs_human',
            'ready_for_review',
            'failed',
            'cancelled',
            'completed'
        )),
    CONSTRAINT ai_tasks_discord_message_unique
        UNIQUE (bot_id, discord_message_id)
);

CREATE INDEX IF NOT EXISTS idx_ai_tasks_bot_status_created
    ON ai_tasks (bot_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_tasks_requester_created
    ON ai_tasks (requester_discord_user_id, created_at DESC);
