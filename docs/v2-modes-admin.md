# ver2.0 Modes Admin

## Purpose

This phase adds the admin foundation for DB-backed modes. It does not switch the bot runtime away from existing JSON behavior, and it does not change the old JSON editing screens.

The admin UI edits these PostgreSQL tables:

- `modes`
- `mode_trigger_conditions`
- `mode_reply_choices`
- `mode_exit_conditions`
- `mode_states` as read-only state display
- `counters` for counter-based trigger conditions

## Mode List

`GET /guilds/{guild_id}/modes` shows modes for one guild.

The list supports:

- keyword search over mode name, mode key, and description
- enabled/disabled filter
- behavior filter: `reply` or `offline`
- admin-only filter
- ON/OFF toggle with role checks
- Info modal for details

Permissions:

- `viewer`: read-only
- `editor`: create/edit/toggle normal modes
- `guild_admin`: create/edit/toggle `admin_only` modes
- `global_admin`: all mode operations

## Mode Form

`GET/POST /guilds/{guild_id}/modes/new` creates a mode.
`GET/POST /guilds/{guild_id}/modes/{mode_id}` edits a mode.

The form stores:

- mode name
- description
- mode key
- enabled state
- admin-only flag
- deletable flag, if later delete UI uses it
- mode icon image path
- enter/exit messages
- enter/exit GIF paths
- enter/exit notification channels
- reaction channel list
- ignored channel list
- cooldown config JSON

The form intentionally does not store:

- priority
- mode-time display name
- normal-time display name
- normal-time icon

The mode-time display name is the mode name. Normal-time display name and icon should return to fixed bot defaults in the later runtime phase.

## Behavior Type

`behavior_type` is exclusive:

- `reply`
- `offline`

Only one can be selected. During a mode, the future bot runtime should not run other features. This phase only stores that design; it does not change runtime behavior.

`reply` modes can use `mode_reply_choices`.
`offline` modes display that reply choices are unused.

## Reply Choices

Reply choices are stored in `mode_reply_choices`.

Fields:

- choice name
- body text
- image path
- appearance rate
- enabled state

Hayusu can be represented with one reply choice. Narita can be represented with multiple reply choices.

## Trigger Conditions

Trigger conditions are stored in `mode_trigger_conditions`.

Supported condition types in the admin foundation:

- `probability`
- `counter_threshold`
- `period_not_triggered`
- `manual`
- `schedule`

Condition details are stored in `condition_config_json`.
The condition group operator can be `AND` or `OR`.

Examples:

- probability: `{"denominator":112}`
- counter threshold: `{"counter_key":"narita_count","operator":">=","value":22}`
- monthly first-run guard: `{"period":"monthly","reset":"month_start"}`
- monthly 22nd guard: `{"period":"monthly","reset":"day","day":22}`

## Counters

Counter threshold conditions can create a new counter while adding the condition.

New counter fields:

- count name
- count key
- description
- initial value
- reset type

`counter_key` is unique within a guild. If the key already exists, the admin UI rejects the new counter.

## Cooldowns

Modes store cooldown settings in `cooldown_config_json`.

Supported foundation types:

- `none`
- `duration`
- `once_per_period`

Examples:

- Hayusu: `once_per_period`, `monthly`, `month_start`
- Narita: `once_per_period`, `monthly`, `day 22`
- duration cooldown: `{"type":"duration","seconds":600}`

## Exit Conditions

Exit conditions are stored in `mode_exit_conditions`.

The admin foundation supports:

- `duration`, stored as `duration_elapsed`
- `manual`

Examples:

- Hayusu: `{"seconds":180}`
- Shikocchi: `{"seconds":840}`

## Fixed Shikocchi Recovery Message

`まずは女子供から殺す` is not an editable exit message.

It is a fixed preset message used when returning to normal mode. The admin UI only notes this behavior and does not provide an edit field for it.

## Future Runtime Phase

This phase is admin and repository foundation only. The later runtime phase should decide how the bot reads these records, applies mode exclusivity, blocks other features during modes, and handles manual enter/exit actions.
