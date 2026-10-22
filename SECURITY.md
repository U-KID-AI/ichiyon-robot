# Security reporting and operational records

Report vulnerabilities through GitHub's private vulnerability reporting feature
when it is enabled for this repository, or through an established private
maintainer contact. Do not put credentials, exploit details about a live
deployment, private addresses, or raw production logs in public issues or PRs.

Public changes should explain the code defect, its fix, and reproducible tests.
Keep deployment inventories, unresolved live-environment findings, backup
locations, credential state, and operational evidence in restricted storage
outside this public repository. Remove sensitive values before sharing output.

Deleting a file from the current branch does not erase Git history, forks,
caches, or existing downloads. Rotate exposed credentials and verify that old
credentials are rejected. If actual sensitive data was published, assess GitHub
Support's removal process separately; do not rewrite shared history or bypass
branch protection as an automatic cleanup action.
