"""Config and options flows."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.svitlobot_validator.const import (
    CONF_FRESH_SECONDS,
    CONF_GRID_ENTITY,
    CONF_SOURCE_CHAT,
    CONF_TARGET_CHAT,
    CONF_TOKEN,
    CONF_VOLTAGE_ENTITY,
    CONF_VOLTAGE_MAX,
    CONF_VOLTAGE_MIN,
    DOMAIN,
)

from .conftest import GOOD_TOKEN, GRID, SOURCE, TARGET, VOLTAGE, make_entry, setup_entry

USER_INPUT = {
    "name": "Svitlobot",
    CONF_TOKEN: GOOD_TOKEN,
    CONF_SOURCE_CHAT: "@svitlobot_test",
    CONF_TARGET_CHAT: str(TARGET),
    CONF_GRID_ENTITY: GRID,
    CONF_VOLTAGE_ENTITY: VOLTAGE,
    CONF_VOLTAGE_MIN: 170,
    CONF_VOLTAGE_MAX: 280,
    CONF_FRESH_SECONDS: 60,
}


async def _start(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return result


async def test_user_flow_creates_entry(hass: HomeAssistant, bot) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], dict(USER_INPUT))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_TOKEN: GOOD_TOKEN}
    assert result["options"][CONF_SOURCE_CHAT] == SOURCE  # @username resolved to the id
    assert result["options"][CONF_TARGET_CHAT] == TARGET
    assert result["result"].unique_id == str(SOURCE)


async def test_user_flow_errors(hass: HomeAssistant, bot) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**USER_INPUT, CONF_TOKEN: "bad"})
    assert result["errors"] == {CONF_TOKEN: "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_TARGET_CHAT: "-1005555555555"}
    )
    assert result["errors"] == {CONF_TARGET_CHAT: "chat_not_found"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_TARGET_CHAT: str(SOURCE)}
    )
    assert result["errors"] == {CONF_TARGET_CHAT: "same_chat"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_VOLTAGE_MIN: 300}
    )
    assert result["errors"] == {CONF_VOLTAGE_MAX: "bad_range"}


async def test_options_flow(hass: HomeAssistant, bot) -> None:
    hass.states.async_set(GRID, "on")
    entry = make_entry()
    await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    options = {k: v for k, v in USER_INPUT.items() if k not in ("name", CONF_TOKEN)}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**options, CONF_VOLTAGE_MIN: 180})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_VOLTAGE_MIN] == 180
    assert entry.options[CONF_SOURCE_CHAT] == SOURCE
