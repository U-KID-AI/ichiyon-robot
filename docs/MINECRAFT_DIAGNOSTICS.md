# Minecraft read-only diagnostics (offline implementation)

`scripts/minecraft/minecraft_control_api.py` now offers `GET /diagnostics`.
It uses `minecraft_diagnostics.py` from the same directory. No request parameter
selects a command, host, container, port, path or log query. Host process settings
alone select the existing BDS container/port and
`MINECRAFT_DIAGNOSTICS_BROADCAST_CONTAINER`; the latter has no guessed default.
`/usr/bin/docker` and the BDS container's existing `mc-monitor` must be available.
The two optional diagnostics variables are documented in
`scripts/minecraft/minecraft-control-api.env.example`.

The endpoint is disabled without a separate `MINECRAFT_DIAGNOSTICS_SECRET`.
Clients use `X-Minecraft-Diagnostics-Secret`; the restart credential is rejected,
and equal read/write secrets disable diagnostics. Keep both credentials in trusted deployment settings and out of task text/logs. Existing `/status` and `/restart` authorization is unchanged.
Only one diagnostic request runs per API worker; concurrent requests return 429.
Each subprocess has a ten-second limit and each output pipe a 64 KiB limit;
combined logs exceeding 64 KiB are discarded. Exceptions/raw stderr never form
part of the response. The API must remain on the restricted management network.

## Evidence and limits

- Filtered Docker inspect returns only state, running, health, start time and
  restart count. It never requests complete inspect, environment or health logs.
- BDS and MCXboxBroadcast versions are numeric startup banners in the last 200
  lines since the observed container start. Unknown/ambiguous banners return null;
  image tags and configured versions are not runtime version proof. Supported
  banner syntax is `Version: 1.2.3.4` and `MCXboxBroadcast version: 1.2.3`, optionally
  preceded by one bracketed log prefix. Production banner/build formats remain
  unverified; no build identity is inferred from a version or image tag.
- Last 15 minutes / 200 lines of broadcast logs produce only event counts for
  CONNECTREQUEST, CONNECTRESPONSE, CANDIDATEADD, ICE and signaling, and counts of
  lexical success/failure hints. These are not protocol outcome proofs: negation,
  quoted payloads and unrelated text can contain the same words. Missing events
  do not prove absence of failures. No candidate addresses, payloads, tokens,
  cookies, raw lines or subprocess errors are returned or persisted.
- UDP uses the existing fixed `mc-monitor status-bedrock` command against the
  configured real Bedrock port on loopback in the BDS container. Success proves
  a local Bedrock response only. Failure is unconfirmed, not proof that the port
  is closed. Host port publishing, firewall, Internet reachability, Xbox signaling
  and friend joins are **not tested** by this probe.
- Existing `/status`/managed-apply readiness continues to use successful
  `mc-monitor` exit status. Only version/player-count metadata depends on the
  known stdout format; unknown formats retain successful local response while
  returning null metadata. This matches the monitor's
  [Bedrock status implementation](https://github.com/itzg/mc-monitor/blob/main/bedrock_status.go).
- `control_api: responding` proves this endpoint answered; a transport failure
  must be recorded by the future trusted caller as unavailable. Collection is a
  sequence of observations, not an atomic snapshot. `observed_at` is its start.

## Activation and remaining integration

This change does not deploy the Control API. Production access follows the user-authorized scope and existing credentials; AGENTS.md does not impose the old Runner change policy. No automatic diagnostic request is wired into LocalRunner.

Before later activation, install `minecraft_control_api.py`,
`minecraft_cosmetics_apply.py` and `minecraft_diagnostics.py` together, provision
the distinct read-only secret outside the repository,
verify actual container names/port, executable availability and banner formats,
and constrain management-network access. Do not modify worlds, downgrade BDS or
update MCXboxBroadcast to validate diagnostics. Disable the new endpoint by
removing its read-only secret if necessary; no data migration is required.

A separately authorized trusted Runner adapter still needs to fetch only this
fixed endpoint, validate/limit the response, and supply a sanitized snapshot to
tasks without exposing its credential, destination or arbitrary HTTP access.
External UDP probing needs a reviewed fixed destination and vantage point.
Actual broadcast build evidence needs a verified non-secret source/format.
Until those steps and live validation are complete, subsequent AI tasks cannot
autonomously diagnose production through this implementation alone. Human
verification is still required for activation as well as the final friend join.

Offline validation (Python 3.11 or newer, matching the application runtime):
`python scripts/check_minecraft_diagnostics.py` uses synthetic
configuration, fake processes and mocked collection; it contacts no environment.
FastAPI must be installed to run the authentication check; without it that check
is skipped and authentication validation remains incomplete.
Both diagnostic and /status checkers run in `.github/workflows/checks.yml`. Host activation, startup banner formats and actual friend joins still need production/human validation. See [CURRENT_BACKLOG](CURRENT_BACKLOG.md).
