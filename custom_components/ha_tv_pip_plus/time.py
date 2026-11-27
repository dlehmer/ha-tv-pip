"""Time platform for HA TV PiP Plus: per-TV do-not-disturb and turn-on hours."""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING, Any

from .schedule import (
    CONF_DND_END,
    CONF_DND_START,
    CONF_WAKE_END,
    CONF_WAKE_START,
    DEFAULT_DND_END,
    DEFAULT_DND_START,
    DEFAULT_WAKE_END,
    DEFAULT_WAKE_START,
    format_time,
    parse_time,
)
from .schedule_entity import ReceiverScheduleEntity

if TYPE_CHECKING:

    class TimeEntity:
        """Fallback base for unit tests outside Home Assistant."""

    class EntityCategory:
        """Fallback entity category for unit tests outside Home Assistant."""

        CONFIG = "config"


else:
    try:
        from homeassistant.components.time import TimeEntity
        from homeassistant.const import EntityCategory
    except ModuleNotFoundError:

        class TimeEntity:
            """Fallback base for unit tests outside Home Assistant."""

        class EntityCategory:
            """Fallback entity category for unit tests outside Home Assistant."""

            CONFIG = "config"


TIME_SETTINGS = (
    (CONF_DND_START, "Do not disturb starts", DEFAULT_DND_START, "mdi:bell-sleep"),
    (CONF_DND_END, "Do not disturb ends", DEFAULT_DND_END, "mdi:bell-ring"),
    (CONF_WAKE_START, "Turn TV on from", DEFAULT_WAKE_START, "mdi:television"),
    (CONF_WAKE_END, "Turn TV on until", DEFAULT_WAKE_END, "mdi:television-off"),
)


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up the schedule time entities for one TV."""

    async_add_entities(
        [
            ReceiverScheduleTime(
                hass, entry, key=key, name=name, fallback=fallback, icon=icon
            )
            for key, name, fallback, icon in TIME_SETTINGS
        ]
    )


class ReceiverScheduleTime(ReceiverScheduleEntity, TimeEntity):
    """One edge of a do-not-disturb or turn-on window."""

    def __init__(
        self,
        hass: Any,
        entry: Any,
        *,
        key: str,
        name: str,
        fallback: str,
        icon: str,
    ) -> None:
        super().__init__(hass, entry, key=key, name=name, icon=icon)
        self._option_key = key
        self._fallback = fallback
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def native_value(self) -> time:
        return parse_time(self.settings[self._option_key], self._fallback)

    async def async_set_value(self, value: time) -> None:
        await self._async_set(self._option_key, format_time(value, self._fallback))
