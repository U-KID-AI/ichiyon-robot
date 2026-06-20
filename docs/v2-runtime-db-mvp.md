# ver2.0 Runtime DB MVP

## Purpose

This phase connects the bot runtime to PostgreSQL for the first MVP path while keeping JSON as the default and safest backend.

The existing JSON behavior remains the default:

```env
ICHIYON_DATA_BACKEND=json
```

`ICHIYON_DATA_BACKEND` accepts:

- `json`
- `db`

If the variable is not set, the bot uses `json`.

## DB Runtime Scope

When `ICHIYON_DATA_BACKEND=db` and the message belongs to a guild, the bot reads these DB-backed features:

- NG words
- mention reactions with `reaction_kind=random`
- auto reactions
- feature flags for those features
- assigned special effect tags for matched NG words, mention reaction choices, and auto reactions

Special effect tags are loaded and logged, but their effects are not executed in this phase.

## Feature Flags

Feature flags are checked per guild:

- `ng_words`
- `mention_reactions`
- `reactions`

If a feature flag row does not exist, the runtime treats the feature as ON.

When a feature is OFF in DB backend, that feature is not executed.

## Mention Reactions

DB mention reactions run only for bot mentions.

Supported in this phase:

- `reaction_kind=random` / `random_draw`
- `enabled=true`
- `match_type=exact`
- `match_type=prefix`
- `match_type=regex`
- weighted choice from enabled `mention_reaction_choices`
- text and image response using the existing send helper

`reaction_kind=search` is intentionally not executed yet. Deck search runtime is a later phase.

Multiple matches are ordered by:

1. longer keyword/pattern
2. match type priority: `exact`, `prefix`, `regex`
3. older creation timestamp

## Auto Reactions

DB auto reactions run for normal guild messages.

Supported:

- `enabled=true`
- `match_type=exact`
- `match_type=prefix`
- `match_type=contains`
- `match_type=regex`
- response text
- image path
- emoji reaction

Multiple matches are ordered by:

1. higher priority
2. longer trigger
3. older creation timestamp

If a DB NG word matches, auto reactions are not executed.

## Template Variables

Response text supports minimal template replacement:

- `{user_name}`
- `{user_mention}`
- `{message_text}`
- `{match_1}`, `{match_2}`, ... for regex capture groups

Unknown placeholders are left as-is.

## Guild and DM Behavior

DB backend requires a guild ID. If a message has no guild, the bot falls back to the existing JSON path.

## Not Connected Yet

These are intentionally not connected in this phase:

- search-type mention reactions, including deck search
- mode runtime from DB
- auto posts runtime from DB
- execution of special effect tag behavior
- production cutover procedure

## Safety Notes

Do not put tokens, `.env`, production data, or `data/backups` into Git.

Production switching remains a later phase after seeded data, operational checks, and rollback steps are prepared.
