"""Constants for Svitlobot Validator."""

DOMAIN = "svitlobot_validator"

CONF_TOKEN = "token"
CONF_SOURCE_CHAT = "source_chat"
CONF_TARGET_CHAT = "target_chat"
CONF_GRID_ENTITY = "grid_entity"
CONF_VOLTAGE_ENTITY = "voltage_entity"
CONF_VOLTAGE_MIN = "voltage_min"
CONF_VOLTAGE_MAX = "voltage_max"
CONF_FRESH_SECONDS = "fresh_seconds"

DEFAULT_VOLTAGE_MIN = 170
DEFAULT_VOLTAGE_MAX = 280
DEFAULT_FRESH_SECONDS = 60

# Posts older than this (HA was down, backlog after a restart) are not checked:
# the sensors would describe a different moment.
MAX_POST_AGE_SECONDS = 15 * 60

SERVICE_CHECK = "check"
ATTR_TEXT = "text"

VERDICT_NONE = "none"
VERDICT_FALSE_ALARM = "false_alarm"
VERDICT_OUT_OF_RANGE = "out_of_range"
VERDICT_CONFIRMED = "confirmed"
VERDICTS = [VERDICT_NONE, VERDICT_FALSE_ALARM, VERDICT_OUT_OF_RANGE, VERDICT_CONFIRMED]
