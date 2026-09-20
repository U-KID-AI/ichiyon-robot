ALTER TABLE ai_tasks
    DROP CONSTRAINT ai_tasks_status_safe,
    ADD CONSTRAINT ai_tasks_status_safe CHECK (status IN (
        'queued', 'running', 'testing', 'deploying', 'needs_human',
        'ready_for_review', 'failed', 'cancelled', 'completed'
    )),
    ADD COLUMN deployed_commit_sha VARCHAR(40),
    ADD COLUMN deployment_summary TEXT,
    ADD COLUMN deployment_started_at TIMESTAMPTZ,
    ADD COLUMN deployed_at TIMESTAMPTZ;

ALTER TABLE ai_tasks
    ADD CONSTRAINT ai_tasks_deployed_commit_sha_format CHECK (
        deployed_commit_sha IS NULL OR deployed_commit_sha ~ '^[0-9a-fA-F]{40}$'
    ),
    ADD CONSTRAINT ai_tasks_deployment_summary_length CHECK (
        deployment_summary IS NULL OR char_length(deployment_summary) <= 4000
    ),
    ADD CONSTRAINT ai_tasks_deployed_at_requires_sha CHECK (
        deployed_at IS NULL OR deployed_commit_sha IS NOT NULL
    );
