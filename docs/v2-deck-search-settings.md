# ver2.0 Deck Search Settings

## Purpose

This phase adds the admin foundation for the fixed search-type mention reaction `デッキ検索`.

It does not change bot runtime behavior, does not replace existing JSON processing, and does not implement the actual X search, QR detection, card image parsing, or class detection logic.

## Placement

Deck search is not shown in the first server feature list.

It is managed under:

`メンション反応 > 検索 > デッキ検索`

In the DB it is represented as a row in `mention_reactions`:

- `reaction_kind = search`
- `is_system = true`
- `reaction_key = deck_search`
- `config_json.search_type = deck_search`

The normal admin UI cannot create arbitrary `search` type mention reactions.

## Admin Settings

The deck search settings form is shown on the mention reaction edit page when the row is a search-type deck search reaction.

Editable settings:

- enabled state
- keyword / pattern
- match type
- allowed channel IDs
- max result count
- message shown when the channel is not allowed
- missing format behavior
- description

Not editable in this phase:

- search implementation
- X search behavior
- QR/image detection
- class/card judgment rules
- arbitrary creation of other search-type features

## config_json

Detailed search settings are stored in `mention_reactions.config_json`.

Example:

```json
{
  "search_type": "deck_search",
  "allowed_channel_ids": ["123", "456"],
  "max_results": 3,
  "deny_message": "このチャンネルではデッキ検索は使えません。",
  "missing_format_behavior": "ask_format"
}
```

The admin form generates this JSON from form fields so invalid JSON is not saved by manual input.

## Fixed Data Helper

If the deck search row does not exist, guild admins can create the fixed row from the mention reactions page.

Initial values:

- name: `デッキ検索`
- keyword: `デッキ検索`
- match type: `prefix`
- reaction kind: `search`
- system flag: `true`
- deletable flag: `false`
- enabled: `false`
- config JSON: `search_type = deck_search`

The initial enabled state is `false` so each guild can review channel restrictions and response policy before enabling it.

## Permissions

- `viewer`: read-only
- `editor`: edit ordinary deck search settings unless the reaction is admin-only
- `guild_admin`: create the fixed deck search row and edit admin-only deck search settings
- `global_admin`: all operations through the existing role hierarchy

## Later Phases

Later phases should implement the actual runtime use of these settings, including channel checks, result formatting, X search, and QR/class/card judgment.
