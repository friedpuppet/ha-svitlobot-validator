# Svitlobot Validator

> Personal project, published only so it can be installed through HACS as a custom repository.
> Use it if it's useful to you, but there's no support and no promise of stability.

A Home Assistant integration that double-checks [Svitlobot](https://svitlobot.in.ua)'s Telegram posts.
Svitlobot learns about power from a device pinging it, so it says "🔴 Світло зникло" whenever that device goes
dark, including when the grid is still there but the voltage has sagged too far. This integration
reads the Svitlobot channel through your own bot and, for every "Світло зникло" post, checks it against:

- a **grid sensor**: a binary sensor that is on while the grid is usable (e.g. an inverter accepting it);
- a **voltage meter powered from the grid** (e.g. a Zigbee meter before the inverter). While it keeps
  reporting, the grid is physically present. A mains-powered meter goes silent in an outage, but HA may
  keep its last value for hours, so "alive" means it reported within *Meter counts as alive for* (default 60 s).

| Grid sensor | Meter | Post to the target chat |
|---|---|---|
| on | — | "⚠️ Електрика насправді є, повідомлення від Світлобота є, ймовірно, помилковим." + voltage |
| off | alive | "⚡ Електрика є, але параметри за межами допустимих." + the voltage vs. the allowed range, and the range seen since the grid sensor went off |
| off | silent | nothing (a real outage) |

After a brownout post, once the grid sensor turns back on, it posts "✅ Напруга повернулась у норму: 185 В.
Чекайте оновлення від Світлобота." If the meter goes silent first (the brownout became a real outage), that
post is skipped.

Each post links to the Svitlobot post it answers. Posts older than 15 minutes (e.g. a backlog after HA was
down) are not checked.

## Setup

1. Create a bot with @BotFather. Add it as an **admin** to the Svitlobot channel (to read its posts) and
   to the chat that should get the verdicts.
2. Add the integration: bot token, Svitlobot channel and target chat (`-100…` id or `@username`), grid
   sensor, meter voltage, allowed voltage range (default 170–280 V, the usual inverter UPS range).

The bot uses long polling (`getUpdates`), so it must not have a webhook set, and nothing else should poll
updates with the same token.

## Entities and services

- `sensor.<name>_last_verdict`: `none` / `false_alarm` / `out_of_range` / `confirmed`; attributes
  `checked_at`, `post_link`, `voltage`, `message`, `brownout`.
- `button.<name>_send_test_message`: posts a test message to the target chat.
- `svitlobot_validator.check` (`text`): check a text as if Svitlobot had posted it, for testing (a bot
  never receives its own posts). Returns the verdicts.
