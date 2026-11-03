"""The verdict on Svitlobot's posts, judged by the meter before the inverter."""

from datetime import timedelta

from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.svitlobot_validator.const import DOMAIN

from .conftest import (
    OUTAGE,
    OWNER,
    PINGER,
    RESTORED,
    TARGET,
    VERDICT,
    VOLTAGE,
    channel_post,
    make_entry,
    setup_entry,
)

LINK = "https://t.me/svitlobot_test/1605"
FALSE_ALARM = "⚠️ Мережа є: 226 В. Повідомлення Світлобота, ймовірно, помилкове."


def hhmm() -> str:
    return dt_util.as_local(dt_util.utcnow()).strftime("%H:%M")


async def test_false_alarm_goes_to_channel_and_owner(hass: HomeAssistant, bot, freezer) -> None:
    hass.states.async_set(PINGER, "off")
    pinger_lost = hhmm()
    freezer.tick(timedelta(minutes=4))
    hass.states.async_set(VOLTAGE, "226.0")
    await setup_entry(hass, make_entry())

    await bot.feed(hass, [channel_post(OUTAGE)])

    assert bot.sent == [
        (TARGET, FALSE_ALARM),
        (
            OWNER,
            "🔧 Світлобот написав «Світло зникло», але лічильник перед інвертором показує 226 В. "
            f"Ймовірно, збій у вас.\nПінгер не відповідає з {pinger_lost}.",
        ),
    ]
    state = hass.states.get(VERDICT)
    assert state.state == "false_alarm"
    assert state.attributes["post_link"] == LINK
    assert state.attributes["brownout"] is False


async def test_false_alarm_with_pinger_online(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(PINGER, "on")
    hass.states.async_set(VOLTAGE, "226")
    await setup_entry(hass, make_entry())

    await bot.feed(hass, [channel_post(OUTAGE)])
    assert bot.sent[1][1].endswith("Пінгер на зв'язку — схоже, збій Світлобота.")


async def test_no_owner_configured(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(VOLTAGE, "226")
    await setup_entry(hass, make_entry(owner_chat=None, pinger_entity=None))

    await bot.feed(hass, [channel_post(OUTAGE)])
    assert bot.sent == [(TARGET, FALSE_ALARM)]


async def test_brownout_reported_then_summed_up(hass: HomeAssistant, bot, freezer) -> None:
    """Today's profile: the voltage sagged to 140 V, came back, then Svitlobot said the power is back."""
    for volts in ("186", "177"):
        hass.states.async_set(VOLTAGE, volts)
        freezer.tick(10)
    started = hhmm()
    for volts in ("166", "149", "140", "145"):
        hass.states.async_set(VOLTAGE, volts)
        freezer.tick(10)
    await async_wait_recording_done(hass)
    await setup_entry(hass, make_entry())

    await bot.feed(hass, [channel_post(OUTAGE)])
    assert bot.sent == [
        (
            TARGET,
            "⚡ Мережа є, але її параметри поза нормою.\n"
            "Напруга 145 В — нижча за допустиму.\n"
            f"Від {started} напруга коливається в межах 140–166 В.",
        )
    ]
    assert hass.states.get(VERDICT).state == "out_of_range"
    assert hass.states.get(VERDICT).attributes["brownout"] is True

    freezer.tick(timedelta(minutes=10))
    hass.states.async_set(VOLTAGE, "160")
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set(VOLTAGE, "172")
    ended = hhmm()
    freezer.tick(timedelta(minutes=3))
    hass.states.async_set(VOLTAGE, "185")
    await async_wait_recording_done(hass)

    await bot.feed(hass, [channel_post(RESTORED)])
    assert bot.sent[-1] == (
        TARGET,
        f"✅ Напруга в нормі: 185 В.\nЗ {started} до {ended} напруга була поза нормою: 140–166 В.",
    )
    assert hass.states.get(VERDICT).attributes["brownout"] is False

    # Only once.
    await bot.feed(hass, [channel_post(RESTORED)])
    assert len(bot.sent) == 2


async def test_restored_while_still_out_of_range(hass: HomeAssistant, bot, freezer) -> None:
    hass.states.async_set(VOLTAGE, "150")
    started = hhmm()
    await setup_entry(hass, make_entry())
    await bot.feed(hass, [channel_post(OUTAGE)])

    freezer.tick(timedelta(minutes=5))
    hass.states.async_set(VOLTAGE, "165")
    await async_wait_recording_done(hass)
    await bot.feed(hass, [channel_post(RESTORED)])
    assert bot.sent[-1] == (
        TARGET,
        f"⚡ Напруга досі поза нормою: 165 В — нижча за допустиму.\nВід {started} напруга коливається в межах 150–165 В.",
    )


async def test_high_voltage(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(VOLTAGE, "291")
    await async_wait_recording_done(hass)
    await setup_entry(hass, make_entry())

    await bot.feed(hass, [channel_post(OUTAGE)])
    assert "Напруга 291 В — вища за допустиму." in bot.sent[0][1]
    assert "напруга коливається на рівні 291 В." in bot.sent[0][1]


async def test_real_outage_is_silent(hass: HomeAssistant, bot, freezer) -> None:
    hass.states.async_set(VOLTAGE, "220")
    await setup_entry(hass, make_entry())

    freezer.tick(timedelta(seconds=61))  # the meter lost power and went quiet
    await bot.feed(hass, [channel_post(OUTAGE)])

    assert bot.sent == []
    assert hass.states.get(VERDICT).state == "confirmed"


async def tick(hass: HomeAssistant, freezer, seconds: float) -> None:
    """Let time pass and run the watch's timer."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)  # the check reads the meter's history in the recorder's thread
    await hass.async_block_till_done()


async def start_brownout_then_lose_the_meter(hass: HomeAssistant, bot, freezer) -> tuple[str, str]:
    """A reported brownout at 150 V, then the meter goes silent: the outage post."""
    hass.states.async_set(VOLTAGE, "150")
    started = hhmm()
    await setup_entry(hass, make_entry())
    await bot.feed(hass, [channel_post(OUTAGE)])
    assert len(bot.sent) == 1

    await tick(hass, freezer, 90)
    return started, started  # the stretch started, and the meter last reported, at the same moment


async def test_brownout_turning_into_outage_is_posted_once(hass: HomeAssistant, bot, freezer) -> None:
    started, lost = await start_brownout_then_lose_the_meter(hass, bot, freezer)
    assert bot.sent[1] == (
        TARGET,
        f"🔴 Мережа зникла о {lost}.\nПеред тим з {started} напруга була поза нормою: 150 В.",
    )
    attributes = hass.states.get(VERDICT).attributes
    assert attributes["brownout"] is False
    assert attributes["outage_since"] is not None

    for _ in range(3):
        await tick(hass, freezer, 30)
    assert len(bot.sent) == 2

    # Svitlobot's own all-clear is right after a real outage: nothing to add.
    hass.states.async_set(VOLTAGE, "225")
    await bot.feed(hass, [channel_post(RESTORED)])
    assert len(bot.sent) == 2
    assert hass.states.get(VERDICT).attributes["outage_since"] is None


async def test_outage_then_back_out_of_range(hass: HomeAssistant, bot, freezer) -> None:
    """The relay keeps the line off, so the pinger and Svitlobot stay silent: we say the grid is back."""
    await start_brownout_then_lose_the_meter(hass, bot, freezer)
    await tick(hass, freezer, 600)

    hass.states.async_set(VOLTAGE, "160")
    back = hhmm()
    await tick(hass, freezer, 30)
    assert bot.sent[2] == (
        TARGET,
        f"⚡ Мережа повернулась о {back}, але напруга поза нормою.\nНапруга 160 В — нижча за допустиму.",
    )
    attributes = hass.states.get(VERDICT).attributes
    assert attributes["brownout"] is True
    assert attributes["outage_since"] is None

    await tick(hass, freezer, 30)
    assert len(bot.sent) == 3

    hass.states.async_set(VOLTAGE, "200")
    ended = hhmm()
    freezer.tick(30)  # Svitlobot posts a little later
    await async_wait_recording_done(hass)
    await bot.feed(hass, [channel_post(RESTORED)])
    assert bot.sent[3] == (
        TARGET,
        f"✅ Напруга в нормі: 200 В.\nЗ {back} до {ended} напруга була поза нормою: 160 В.",
    )


async def test_outage_then_back_in_range_is_silent(hass: HomeAssistant, bot, freezer) -> None:
    await start_brownout_then_lose_the_meter(hass, bot, freezer)

    hass.states.async_set(VOLTAGE, "225")
    await tick(hass, freezer, 30)
    assert len(bot.sent) == 2
    attributes = hass.states.get(VERDICT).attributes
    assert attributes["brownout"] is False
    assert attributes["outage_since"] is None


async def test_no_outage_post_right_after_a_restart(hass: HomeAssistant, bot, freezer) -> None:
    """After a reload the meter needs a moment to report; a stale reading then isn't an outage yet."""
    hass.states.async_set(VOLTAGE, "150")
    entry = make_entry()
    await setup_entry(hass, entry)
    await bot.feed(hass, [channel_post(OUTAGE)])

    freezer.tick(timedelta(seconds=61))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    assert len(bot.sent) == 1

    await tick(hass, freezer, 40)
    assert bot.sent[1][1].startswith("🔴 Мережа зникла о ")


async def test_brownout_survives_reload(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(VOLTAGE, "150")
    entry = make_entry()
    await setup_entry(hass, entry)
    await bot.feed(hass, [channel_post(OUTAGE)])

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(VERDICT).state == "out_of_range"
    assert hass.states.get(VERDICT).attributes["brownout"] is True

    hass.states.async_set(VOLTAGE, "200")
    await async_wait_recording_done(hass)
    await bot.feed(hass, [channel_post(RESTORED)])
    assert bot.sent[-1][1].startswith("✅ Напруга в нормі: 200 В.")


async def test_range_survives_restart_gap(hass: HomeAssistant, bot, freezer) -> None:
    """An HA restart leaves an "unavailable" gap in the meter's history; the stretch goes on."""
    hass.states.async_set(VOLTAGE, "190")
    freezer.tick(60)
    hass.states.async_set(VOLTAGE, "150")
    started = hhmm()
    freezer.tick(timedelta(minutes=3))
    hass.states.async_set(VOLTAGE, "135")
    freezer.tick(timedelta(minutes=3))
    hass.states.async_set(VOLTAGE, "unavailable")
    freezer.tick(30)
    hass.states.async_set(VOLTAGE, "160")
    await async_wait_recording_done(hass)
    await setup_entry(hass, make_entry())

    await bot.feed(hass, [channel_post(OUTAGE)])
    assert f"Від {started} напруга коливається в межах 135–160 В." in bot.sent[0][1]


async def test_other_posts_ignored(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(VOLTAGE, "226")
    await setup_entry(hass, make_entry())

    await bot.feed(
        hass,
        [
            channel_post(RESTORED),  # no brownout was reported
            channel_post("🗓️Графік відключень, 3.1 група\n 9:00 - 12:30"),
            channel_post(OUTAGE, chat_id=-1009999999999),  # another channel
            channel_post(OUTAGE, age=20 * 60),  # backlog
        ],
    )
    assert bot.sent == []
    assert hass.states.get(VERDICT).state == "none"


async def test_offset_persists(hass: HomeAssistant, bot) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    update = channel_post(RESTORED)
    await bot.feed(hass, [update])
    assert bot.offsets[-1] == update["update_id"] + 1

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert bot.offsets[-1] == update["update_id"] + 1


async def test_check_service_and_test_button(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(VOLTAGE, "226")
    await setup_entry(hass, make_entry(owner_chat=None))

    response = await hass.services.async_call(
        DOMAIN, "check", {"text": OUTAGE}, blocking=True, return_response=True
    )
    assert response == {"verdicts": ["false_alarm"]}
    assert bot.sent == [(TARGET, FALSE_ALARM)]

    await hass.services.async_call("button", "press", {"entity_id": "button.svitlobot_send_test_message"}, blocking=True)
    assert bot.sent[-1] == (TARGET, "🧪 Тестове повідомлення від Svitlobot Validator")
