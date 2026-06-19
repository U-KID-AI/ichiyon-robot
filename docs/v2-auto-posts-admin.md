# ver2.0 Auto Posts Admin

## Purpose

This phase adds the DB-backed admin foundation for auto posts. It does not change current bot behavior, does not replace existing JSON processing, and does not switch the old JSON admin screens to DB operation.

The admin UI edits `auto_posts`.

## Routes

- `GET /guilds/{guild_id}/auto-posts`
- `POST /guilds/{guild_id}/auto-posts/{post_id}/toggle`
- `GET /guilds/{guild_id}/auto-posts/new`
- `POST /guilds/{guild_id}/auto-posts/new`
- `GET /guilds/{guild_id}/auto-posts/{post_id}`
- `POST /guilds/{guild_id}/auto-posts/{post_id}`

## Permissions

- `viewer`: read-only list and detail access
- `editor`: create, edit, and toggle auto posts
- `guild_admin`: same as editor for this phase
- `global_admin`: all operations

## List View

The list shows:

- enabled state
- post name
- channel ID
- body summary
- image path presence
- schedule summary
- last posted timestamp
- next run timestamp placeholder, if a later runtime phase provides it
- edit and Info actions

Filters:

- keyword over name/body
- enabled state
- image presence
- channel ID

## Form Fields

The create/edit form stores:

- post name
- body
- image path
- channel ID
- enabled state
- schedule type
- month
- day
- weekday
- time
- timezone

The admin UI stores schedule details as JSON text in `schedule_value`.
`repeat_rule` is preserved for future use, but the current form does not require manual editing of it.

## Schedule Types

Supported schedule types:

- `once`
- `yearly`
- `monthly`
- `weekly`
- `daily`

Validation:

- post name is required
- body or image path is required
- channel ID is required
- schedule type must be allowed
- `once` and `yearly` require month/day
- `monthly` requires day
- `weekly` requires weekday
- time must be `HH:MM`
- timezone defaults to `Asia/Tokyo`

## 6/30 Example

Example for the annual 6/30 post:

- name: `6/30 サ終やめませんか？`
- body: `サ終やめませんか？`
- schedule type: `yearly`
- month: `6`
- day: `30`
- time: any configured `HH:MM`
- timezone: `Asia/Tokyo`
- channel ID: configured per guild in the admin UI

No automatic seed data is inserted in this phase.

## Future Runtime Phase

This phase only stores DB settings. A later runtime phase should decide how the bot reads `auto_posts`, calculates next run timestamps, prevents duplicate posts, and updates `last_posted_at`.
