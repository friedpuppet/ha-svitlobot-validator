# energy-tg-bot — Svitlobot Validator (Home Assistant integration)

Subproject of `homeassistant`. This is the HA custom integration **`svitlobot_validator`** ("Svitlobot
Validator"). It reads @SvitloUkraineBot's posts in the channel `@svitlobot_kyiv_dashkevycha_4` through our bot and checks every
"🔴 … Світло зникло" against `binary_sensor.e_elektrika` and the ZHA meter before the inverter
(`sensor.lichilnik_pered_invertorom_napruga`). Behaviour is in `README.md`.

## Shared Home Assistant context

Read `../AGENTS.md` (host, SSH, API token, backups), `../electricity.md` (grid sensor chain) and
`../grid-load-shedding/AGENTS.md` (the sibling integration this one mirrors: layout, release, HACS via WS).

## Telegram

- Bot **@kyiv_dashkevycha_4_svitlo_bot** (id `8280208294`). Its token is in the disabled Power Watchdog entry
  (`01KH5T56Z5FVVD69ZTAKHWT2ZY`, `.storage/core.config_entries`, `options.telegram_token`) and in this entry's data.
  The bot is an admin in all three chats below. It uses `getUpdates` long polling: there must be no webhook, and
  nothing else may poll this token.
- Chats:
  | Chat | Id | Role |
  |---|---|---|
  | СвітлоБот ⚡ Київ, Дашкевича, 4 (`@svitlobot_kyiv_dashkevycha_4`) | `-1002234976277` | **source**: Svitlobot posts here. **Never post here** without the user's explicit go-ahead |
  | Тест для світлобота | `-1003971371667` | **target** for now: every verdict and test goes here |
  | s4_svitlo_test (`@d4_svitlo_test`) | `-1003716931187` | the user's older test channel, unused |
- A bot never receives its own posts. To test, use the `svitlobot_validator.check` service, or have the user post into
  the source channel.
- Svitlobot formats (2026-10): `🔴 HH:MM Світло зникло`, `🟢 HH:MM Світло з'явилося`, `🗓️Графік відключень…`. Only
  "Світло зникло" is checked (the user's choice). History: `https://t.me/s/svitlobot_kyiv_dashkevycha_4`.

## Why (the 2026-10-08 brownout)

At 14:12 Kyiv Svitlobot posted an outage. In fact the meter before the inverter kept reporting 140–185 V. The inverter
(UPS range ≈170–280 V) dropped the grid at ~177 V (`e_elektrika` off 11:12:46–11:32:37 UTC), and the pinger ESP went
dark too. The user then posted by hand "Світло номінально є, але напруга занизька". The ZHA meter
is mains-powered, but ZHA marks such devices unavailable only after ~2 h, so "meter alive" is judged by
`last_reported` ≤ 60 s (it reports every ~7 s).

## Status

- Code and tests are done (`uv run pytest -q`). Version **0.1.0**.
- Repo: **friedpuppet/ha-svitlobot-validator** (public, for HACS only; same "personal project" rules and PAT
  `~/.config/github/token-grid-load-shedding` as `../grid-load-shedding/AGENTS.md`).

## Dev

```sh
cd ~/work/claude/homeassistant/energy-tg-bot
uv sync
uv run pytest -q
```
