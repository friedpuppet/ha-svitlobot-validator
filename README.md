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

The meter shows what the building gets, so it decides; the grid sensor (the inverter's view) only
matters when the meter is silent. A live meter with a normal voltage while Svitlobot reports an outage
means the trouble is on our side (the pinger, or the inverter), not in the grid.

| Meter | Grid sensor | Post to the target chat |
|---|---|---|
| alive, voltage in range | — | "⚠️ Мережа є: 220 В. Повідомлення Світлобота, ймовірно, помилкове." |
| alive, voltage out of range | — | "⚡ Мережа є, але її параметри поза нормою." + "Напруга 145 В — нижча/вища за допустиму." (the range itself isn't quoted: the real limits are on an offline voltage relay) + the voltage range since the grid sensor went off (read from the recorder, so it survives HA restarts) |
| silent | on | "⚠️ Мережа є. Повідомлення Світлобота, ймовірно, помилкове." |
| silent | off / unavailable | nothing (a real outage) |

After a brownout post, once the grid sensor turns back on, it posts "✅ Напруга повернулася в норму: 185 В.
Світлобот невдовзі оновить статус." If the meter goes silent first (the brownout became a real outage), that
post is skipped.

Posts carry no link to the Svitlobot post: the channel is quiet enough that the context is obvious. Posts older than 15 minutes (e.g. a backlog after HA was
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
