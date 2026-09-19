ALTER TABLE ai_tasks
    ADD COLUMN runner_id VARCHAR(128),
    ADD COLUMN claim_token UUID,
    ADD COLUMN claimed_at TIMESTAMPTZ,
    ADD COLUMN heartbeat_at TIMESTAMPTZ,
    ADD COLUMN lease_expires_at TIMESTAMPTZ,
    ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN base_commit_sha VARCHAR(40),
    ADD COLUMN commit_sha VARCHAR(40),
    ADD COLUMN pr_number BIGINT,
    ADD COLUMN pr_url TEXT,
    ADD COLUMN test_summary TEXT,
    ADD COLUMN changed_files_summary TEXT,
    ADD COLUMN discord_reported_at TIMESTAMPTZ;

ALTER TABLE ai_tasks
    ADD CONSTRAINT ai_tasks_attempt_count_nonnegative CHECK (attempt_count >= 0),
    ADD CONSTRAINT ai_tasks_pr_number_positive CHECK (pr_number IS NULL OR pr_number > 0),
    ADD CONSTRAINT ai_tasks_base_commit_sha_format CHECK (base_commit_sha IS NULL OR base_commit_sha ~ '^[0-9a-fA-F]{40}$'),
    ADD CONSTRAINT ai_tasks_commit_sha_format CHECK (commit_sha IS NULL OR commit_sha ~ '^[0-9a-fA-F]{40}$'),
    ADD CONSTRAINT ai_tasks_current_step_length CHECK (current_step IS NULL OR char_length(current_step) <= 500),
    ADD CONSTRAINT ai_tasks_progress_summary_length CHECK (progress_summary IS NULL OR char_length(progress_summary) <= 4000),
    ADD CONSTRAINT ai_tasks_error_message_length CHECK (error_message IS NULL OR char_length(error_message) <= 4000),
    ADD CONSTRAINT ai_tasks_test_summary_length CHECK (test_summary IS NULL OR char_length(test_summary) <= 8000),
    ADD CONSTRAINT ai_tasks_changed_files_summary_length CHECK (changed_files_summary IS NULL OR char_length(changed_files_summary) <= 8000);

CREATE INDEX idx_ai_tasks_status_lease
    ON ai_tasks (status, lease_expires_at);
