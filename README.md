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

"Alive, voltage in range" looks back first: if the meter was out of range at any point since the time in
Svitlobot's post («🔴 **09:09** Світло зникло», a minute early, at most 30 minutes back), it's a brownout that has
just ended, not a false alarm: "⚡ Мережа є, але її параметри були поза нормою." + "Зараз напруга 185 В, а від 09:10
вона опускалася до 174 В." (no private note), and the brownout is watched as usual.

The allowed range itself isn't quoted in the posts: the real limits are on an offline voltage relay. The
start of a brownout is the first out-of-range reading after the last normal one, read from the recorder,
so it survives HA restarts.

**After a post that says the grid is there** (a false alarm or a brownout) the meter is watched (every 30 s) until
Svitlobot posts «Світло з'явилося»:

- the meter goes silent (a real outage now; Svitlobot has nothing new to say, so the channel would still read "the
  grid is there"): "🔴 Мережа зникла о 09:31.", after a brownout also "Перед тим з 09:00 напруга була поза нормою:
  251–263 В.";
- after that the meter comes back but out of range (a voltage relay keeps the line off, so the pinger and Svitlobot
  stay silent): "⚡ Мережа повернулась о 09:40, але напруга поза нормою." + "Напруга 255 В — вища за допустиму.".
  The brownout is watched again from there;
- the meter comes back in range: nothing at first; Svitlobot's own «Світло з'явилося» will be right. If Svitlobot
  stays silent for 10 minutes of a good grid (a stuck pinger, Wi-Fi or Svitlobot itself): "✅ Мережа повернулась о
  16:12, напруга в нормі: 230 В.", plus a private note with the pinger's state. When a brownout ends this way with no
  outage in between, the post adds "З 09:09 до 16:12 напруга була поза нормою: …". After an outage it doesn't: the
  outage post already gave the brownout's range, and the summary covers only what came after the outage. If the *inverter grid* sensor is set
  and off (the building has the grid, the flat doesn't), only a private note goes out, and the channel post waits
  until the inverter takes the grid. After the post the grid is watched as after a false alarm.

Silence means no report for *Meter counts as alive for*, so the outage post comes 1–1.5 min late. Nothing is decided
in the first *Meter counts as alive for* after HA starts, while the meter hasn't reported yet.

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
