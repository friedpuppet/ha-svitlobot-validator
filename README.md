# Svitlobot Validator

> Personal project, published only so it can be installed through HACS as a custom repository.
> Use it if it's useful to you, but there's no support and no promise of stability.

A Home Assistant integration that double-checks [Svitlobot](https://svitlobot.in.ua)'s Telegram posts.
Svitlobot learns about power from a device pinging it, so it says "🔴 Світло зникло" whenever that device goes
dark, including when the grid is still there but the voltage has sagged too far. This integration
reads the Svitlobot channel through your own bot and checks Svitlobot's posts against a **voltage meter
powered straight from the grid** (e.g. a Zigbee meter before the inverter). While it keeps reporting, the grid
is physically there. A mains-powered meter goes silent in an outage, but HA may keep its last value for a while,
so "alive" means it reported within *Meter counts as alive for* (default 60 s).

Only the meter decides. An inverter-based grid sensor is deliberately not used: an inverter problem isn't a
grid problem.

**"🔴 … Світло зникло"**

| Meter | Post to the target chat | Private note to you |
|---|---|---|
| alive, voltage in range | "⚠️ Мережа є: 220 В. Повідомлення Світлобота, ймовірно, помилкове." | "🔧 Світлобот написав «Світло зникло», але лічильник перед інвертором показує 220 В. Ймовірно, збій у вас." + the pinger's state |
| alive, voltage out of range | "⚡ Мережа є, але її параметри поза нормою." + "Напруга 145 В — нижча/вища за допустиму." + "Від 14:12 напруга коливається в межах 140–166 В." | — |
| silent | nothing (a real outage) | — |

The allowed range itself isn't quoted in the posts: the real limits are on an offline voltage relay. The
start of a brownout is the first out-of-range reading after the last normal one, read from the recorder,
so it survives HA restarts.

**"🟢 … Світло з'явилося" after a reported brownout**: "✅ Напруга в нормі: 185 В." + "З 14:12 до 14:31
напруга була поза нормою: 140–166 В." (or "⚡ Напруга досі поза нормою: …" if it still is). If the meter went
silent in between (the brownout became a real outage), nothing is posted.

Posts carry no link to the Svitlobot post: the channel is quiet enough that the context is obvious. Posts older than 15 minutes (e.g. a backlog after HA was
down) are not checked.

## Setup

1. Create a bot with @BotFather. Add it as an **admin** to the Svitlobot channel (to read its posts) and
   to the chat that should get the verdicts.
2. Add the integration: bot token, Svitlobot channel and target chat (`-100…` id or `@username`), meter
   voltage, allowed voltage range (default 170–280 V, the usual inverter UPS range). Optional: your own user id
   for private notes (press **Start** in the bot first, or it can't message you) and the pinger's binary sensor.

The bot uses long polling (`getUpdates`), so it must not have a webhook set, and nothing else should poll
updates with the same token.

## Entities and services

- `sensor.<name>_last_verdict`: `none` / `false_alarm` / `out_of_range` / `confirmed`; attributes
  `checked_at`, `post_link`, `voltage`, `message`, `brownout`.
- `button.<name>_send_test_message`: posts a test message to the target chat.
- `svitlobot_validator.check` (`text`): check a text as if Svitlobot had posted it, for testing (a bot
  never receives its own posts). Returns the verdicts (`back_to_normal` for a summed-up brownout).
