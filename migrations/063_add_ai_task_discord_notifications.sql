ALTER TABLE ai_tasks
    ADD COLUMN discord_terminal_notified_status VARCHAR(32),
    ADD COLUMN discord_terminal_notified_at TIMESTAMPTZ,
    ADD COLUMN discord_terminal_notification_message_id TEXT,
    ADD CONSTRAINT ai_tasks_discord_terminal_notification_valid CHECK (
        (discord_terminal_notified_status IS NULL
         AND discord_terminal_notified_at IS NULL
         AND discord_terminal_notification_message_id IS NULL)
        OR
        (discord_terminal_notified_status IS NOT NULL
         AND discord_terminal_notified_status IN ('completed', 'failed', 'needs_human', 'cancelled')
         AND discord_terminal_notified_at IS NOT NULL)
    );

CREATE INDEX ai_tasks_discord_terminal_pending_idx
    ON ai_tasks (bot_id, guild_id, discord_channel_id, updated_at, task_id)
    WHERE status IN ('completed', 'failed', 'needs_human', 'cancelled')
      AND discord_terminal_notified_status IS NULL
      AND discord_terminal_notified_at IS NULL;
