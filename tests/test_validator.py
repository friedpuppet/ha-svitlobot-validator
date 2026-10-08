"""The verdict on Svitlobot's outage posts, and the "back to normal" post after a brownout."""

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.svitlobot_validator.const import DOMAIN

from .conftest import GRID, OUTAGE, RESTORED, SOURCE, TARGET, VERDICT, VOLTAGE, channel_post, make_entry, setup_entry

LINK = "https://t.me/svitlobot_test/1605"


async def test_false_alarm_when_grid_is_on(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(GRID, "on")
    hass.states.async_set(VOLTAGE, "226.0")
    await setup_entry(hass, make_entry())

    await bot.feed(hass, [channel_post(OUTAGE)])

    assert bot.sent == [
        (
            TARGET,
            "⚠️ Мережа є: 226 В. Повідомлення Світлобота, ймовірно, помилкове.\n"
            f"Джерело: {LINK}",
        )
    ]
    state = hass.states.get(VERDICT)
    assert state.state == "false_alarm"
    assert state.attributes["post_link"] == LINK


async def test_brownout_reported_then_back_to_normal(hass: HomeAssistant, bot) -> None:
    """Today's profile: the inverter dropped the grid at ~177 V, the meter saw 140–185 V."""
    hass.states.async_set(GRID, "on")
    hass.states.async_set(VOLTAGE, "186")
    await setup_entry(hass, make_entry())

    hass.states.async_set(VOLTAGE, "177")
    hass.states.async_set(GRID, "off")
    for volts in ("166", "149", "140", "145"):
        hass.states.async_set(VOLTAGE, volts)
    await hass.async_block_till_done()

    await bot.feed(hass, [channel_post(OUTAGE)])

    since = dt_util.as_local(dt_util.utcnow()).strftime("%H:%M")
    assert bot.sent == [
        (
            TARGET,
            "⚡ Мережа є, але її параметри поза нормою.\n"
            "Напруга 145 В — нижча за допустиму (170–280 В).\n"
            f"Від {since} напруга коливається в межах 140–177 В.\n"
            f"Джерело: {LINK}",
        )
    ]
    assert hass.states.get(VERDICT).state == "out_of_range"
    assert hass.states.get(VERDICT).attributes["brownout"] is True

    hass.states.async_set(VOLTAGE, "185")
    hass.states.async_set(GRID, "on")
    await hass.async_block_till_done()
    assert bot.sent[-1] == (TARGET, "✅ Напруга повернулася в норму: 185 В. Світлобот невдовзі оновить статус.")
    assert hass.states.get(VERDICT).attributes["brownout"] is False

    # Only once.
    hass.states.async_set(GRID, "off")
    hass.states.async_set(GRID, "on")
    await hass.async_block_till_done()
    assert len(bot.sent) == 2


async def test_high_voltage(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(GRID, "off")
    hass.states.async_set(VOLTAGE, "291")
    await setup_entry(hass, make_entry())

    await bot.feed(hass, [channel_post(OUTAGE)])
    assert "Напруга 291 В — вища за допустиму (170–280 В)." in bot.sent[0][1]
    since = dt_util.as_local(hass.states.get(GRID).last_changed).strftime("%H:%M")
    assert f"Від {since} напруга тримається на рівні 291 В." in bot.sent[0][1]


async def test_voltage_in_range_but_grid_off(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(GRID, "off")
    hass.states.async_set(VOLTAGE, "220")
    await setup_entry(hass, make_entry())

    await bot.feed(hass, [channel_post(OUTAGE)])
    assert "Напруга 220 В — у допустимих межах (170–280 В), але наш датчик мережі її не фіксує." in bot.sent[0][1]


async def test_real_outage_is_silent(hass: HomeAssistant, bot, freezer) -> None:
    hass.states.async_set(GRID, "on")
    hass.states.async_set(VOLTAGE, "220")
    await setup_entry(hass, make_entry())

    hass.states.async_set(GRID, "off")
    freezer.tick(timedelta(seconds=61))  # the meter lost power and went quiet
    await bot.feed(hass, [channel_post(OUTAGE)])

    assert bot.sent == []
    assert hass.states.get(VERDICT).state == "confirmed"


async def test_brownout_turning_into_outage_skips_back_to_normal(hass: HomeAssistant, bot, freezer) -> None:
    hass.states.async_set(GRID, "off")
    hass.states.async_set(VOLTAGE, "150")
    await setup_entry(hass, make_entry())
    await bot.feed(hass, [channel_post(OUTAGE)])
    assert len(bot.sent) == 1

    freezer.tick(timedelta(seconds=90))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(VERDICT).attributes["brownout"] is False

    hass.states.async_set(VOLTAGE, "225")
    hass.states.async_set(GRID, "on")
    await hass.async_block_till_done()
    assert len(bot.sent) == 1


async def test_brownout_survives_reload(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(GRID, "off")
    hass.states.async_set(VOLTAGE, "150")
    entry = make_entry()
    await setup_entry(hass, entry)
    await bot.feed(hass, [channel_post(OUTAGE)])

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(VERDICT).state == "out_of_range"

    hass.states.async_set(VOLTAGE, "200")
    hass.states.async_set(GRID, "on")
    await hass.async_block_till_done()
    assert bot.sent[-1][1].startswith("✅ Напруга повернулася в норму: 200 В.")


async def test_other_posts_ignored(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(GRID, "on")
    hass.states.async_set(VOLTAGE, "226")
    await setup_entry(hass, make_entry())

    await bot.feed(
        hass,
        [
            channel_post(RESTORED),
            channel_post("🗓️Графік відключень, 3.1 група\n 9:00 - 12:30"),
            channel_post(OUTAGE, chat_id=-1009999999999),  # another channel
            channel_post(OUTAGE, age=20 * 60),  # backlog
        ],
    )
    assert bot.sent == []
    assert hass.states.get(VERDICT).state == "none"


async def test_offset_persists(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(GRID, "on")
    entry = make_entry()
    await setup_entry(hass, entry)
    update = channel_post(RESTORED)
    await bot.feed(hass, [update])
    assert bot.offsets[-1] == update["update_id"] + 1

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert bot.offsets[-1] == update["update_id"] + 1


async def test_check_service_and_test_button(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(GRID, "on")
    await setup_entry(hass, make_entry())

    response = await hass.services.async_call(
        DOMAIN, "check", {"text": OUTAGE}, blocking=True, return_response=True
    )
    assert response == {"verdicts": ["false_alarm"]}
    assert bot.sent[-1][1] == "⚠️ Мережа є. Повідомлення Світлобота, ймовірно, помилкове."

    await hass.services.async_call("button", "press", {"entity_id": "button.svitlobot_send_test_message"}, blocking=True)
    assert bot.sent[-1] == (TARGET, "🧪 Тестове повідомлення від Svitlobot Validator")
    assert SOURCE not in [chat for chat, _ in bot.sent]


async def test_brownout_dropped_if_grid_returned_while_down(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(GRID, "off")
    hass.states.async_set(VOLTAGE, "150")
    entry = make_entry()
    await setup_entry(hass, entry)
    await bot.feed(hass, [channel_post(OUTAGE)])
    assert await hass.config_entries.async_unload(entry.entry_id)

    hass.states.async_set(GRID, "on")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(VERDICT).attributes["brownout"] is False
    assert len(bot.sent) == 1
