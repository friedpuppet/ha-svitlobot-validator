# energy-tg-bot — Svitlobot Validator (Home Assistant integration)

Subproject of `homeassistant`. This is the HA custom integration **`svitlobot_validator`** ("Svitlobot
Validator"). It reads @SvitloUkraineBot's posts in the channel `@svitlobot_kyiv_dashkevycha_4` through our bot and checks every
"🔴 … Світло зникло" against the ZHA meter before the inverter
(`sensor.lichilnik_pered_invertorom_napruga`) only. Behaviour is in `README.md`.

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
`last_reported` ≤ 60 s. (Observed later: ZHA marked it unavailable ~2 min after an outage; the check stays) (it reports every ~7 s).

## Status

- Code and tests are done (`uv run pytest -q`). Version **0.2.0**.
- **Installed on the live HA (2026-10-08)** via HACS custom repository (HACS repo id `1410267100`), entry «Світлобот»
  `01M4DQ0BMA0749WWZGGW8ZZQXA` (created through the config flow REST API). Source `-1002234976277`, target
  `-1003971371667`, meter `sensor.lichilnik_pered_invertorom_napruga`, 170–280 V, 60 s.
  170–280 V is a placeholder (the inverter's approximate UPS range from `../electricity.md`); the real limits are on the
  offline voltage relay, and the user will set them in the options flow himself.
  - Entities: `sensor.svitlobot_last_verdict`, `button.svitlobot_send_test_message`; service `svitlobot_validator.check`.
  - State (offset, brownout flag, last verdict): `.storage/svitlobot_validator.<entry_id>`.
  - **Updating**: release (bump `manifest.json` + `pyproject.toml`), then WS `hacs/repository/download` with
    `repository: "1410267100"`, `version: "vX.Y.Z"`, then `ha core check` + `ha core restart`.
  - v0.1.0 never polled: Telegram's `timeout` param clashed with `_call`'s HTTP timeout (tests used a fake bot).
    Fixed in v0.1.1, plus `tests/test_telegram.py` against a mocked API.
  - The brownout post's «Від HH:MM напруга коливається в межах …» comes from the **recorder** (`validator.outage_history`:
    first `off` after the last `on` of the grid sensor within 24 h, then the meter's readings since then), not from
    in-memory tracking: a restart re-writes the state with a new `last_changed` (v0.1.3, the user's call).
  - Real outage 2026-10-08 15:26 Kyiv: Svitlobot's post 1608 was checked the same second → `confirmed`, silent. The ZHA
    meter went `unavailable` ~2 min after the grid dropped (faster than the ~2 h I assumed); the 60 s freshness stays.
  - Since v0.1.4 the posts carry no link to the Svitlobot post and don't quote the 170–280 V range ("нижча/вища за
    допустиму" only): the real limits are set on an offline voltage relay that HA can't see (the user's call). The
    range options still decide below/above/within; `post_link` stays a sensor attribute.
  - **Meter only** since v0.2.0 (the user's call): `e_elektrika` is the *inverter's* view (inverter grid voltage >
    170 V, pinger as fallback; the meter isn't in that chain), so it's not used at all. Meter alive + in range →
    false alarm in the channel **plus a private note to the user** (id `505993907`, @yyyell, `owner_chat`) with the
    pinger's state (`binary_sensor.esp_svitlobot_pinger_local`): a pinger or Svitlobot glitch isn't the channel's
    business. Meter alive + out of range → brownout post. Meter silent → silent.
  - Brownout start = first out-of-range meter reading after the last normal one (recorder, `unavailable` gaps
    skipped). The "back to normal" summary is sent when Svitlobot posts «Світло з'явилося» (the user's call), with
    "З HH:MM до HH:MM напруга була поза нормою: lo–hi В".
  - Verified live 2026-10-08: polling works (offset stored), the test button and `check` (→ `false_alarm`) post into
    the test channel. The brownout and back-to-normal branches haven't been seen live yet.
- Repo: **friedpuppet/ha-svitlobot-validator** (public, for HACS only; same "personal project" rules and PAT
  `~/.config/github/token-grid-load-shedding` as `../grid-load-shedding/AGENTS.md`).

## Dev

```sh
cd ~/work/claude/homeassistant/energy-tg-bot
uv sync
uv run pytest -q
```
