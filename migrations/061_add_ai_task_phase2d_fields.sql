ALTER TABLE ai_tasks
    ADD COLUMN ci_workflow_run_id BIGINT,
    ADD COLUMN review_summary TEXT,
    ADD COLUMN merge_commit_sha VARCHAR(40);

ALTER TABLE ai_tasks
    ADD CONSTRAINT ai_tasks_ci_workflow_run_id_positive
        CHECK (
            ci_workflow_run_id IS NULL
            OR ci_workflow_run_id > 0
        ),
    ADD CONSTRAINT ai_tasks_review_summary_length
        CHECK (
            review_summary IS NULL
            OR char_length(review_summary) <= 2000
        ),
    ADD CONSTRAINT ai_tasks_merge_commit_sha_format
        CHECK (
            merge_commit_sha IS NULL
            OR merge_commit_sha ~ '^[0-9a-fA-F]{40}$'
        );
