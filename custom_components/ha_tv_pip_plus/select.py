"""Select platform for HA TV PiP Plus: per-TV turn-on and do-not-disturb choices."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .schedule import (
    CONF_DND_ENTITY_STATE,
    CONF_WAKE_MODE,
    DND_ENTITY_STATES,
    WAKE_MODES,
)
from .schedule_entity import ReceiverScheduleEntity
from .stills import CONF_WOKEN_TV_VIEW, WOKEN_TV_VIEWS, still_options

if TYPE_CHECKING:

    class SelectEntity:
        """Fallback base for unit tests outside Home Assistant."""

    class EntityCategory:
        """Fallback entity category for unit tests outside Home Assistant."""

        CONFIG = "config"


else:
    try:
        from homeassistant.components.select import SelectEntity
        from homeassistant.const import EntityCategory
    except ModuleNotFoundError:

        class SelectEntity:
            """Fallback base for unit tests outside Home Assistant."""

        class EntityCategory:
            """Fallback entity category for unit tests outside Home Assistant."""

            CONFIG = "config"


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """Set up the schedule select entities for one TV."""

    async_add_entities(
        [
            ReceiverScheduleSelect(
                hass,
                entry,
                key=CONF_WAKE_MODE,
                name="Turn TV on for popups",
                choices=WAKE_MODES,
                icon="mdi:television-classic",
            ),
            ReceiverScheduleSelect(
                hass,
                entry,
                key=CONF_WOKEN_TV_VIEW,
                name="When the TV is turned on, show",
                choices=WOKEN_TV_VIEWS,
                icon="mdi:image-frame",
                read=lambda entry: still_options(entry.options)[CONF_WOKEN_TV_VIEW],
            ),
            ReceiverScheduleSelect(
                hass,
                entry,
                key=CONF_DND_ENTITY_STATE,
                name="Do not disturb when control entity is",
                choices=DND_ENTITY_STATES,
                icon="mdi:toggle-switch",
            ),
        ]
    )


class ReceiverScheduleSelect(ReceiverScheduleEntity, SelectEntity):
    """A choice stored in this TV's options."""

    def __init__(
        self,
        hass: Any,
        entry: Any,
        *,
        key: str,
        name: str,
        choices: tuple[str, ...],
        icon: str,
        read: Any = None,
    ) -> None:
        super().__init__(hass, entry, key=key, name=name, icon=icon)
        self._read = read
        self._option_key = key
        self._attr_options = list(choices)
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def current_option(self) -> str:
        if self._read is not None:
            return str(self._read(self.entry))
        return str(self.settings[self._option_key])

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            raise ValueError(f"Unsupported option: {option}")
        await self._async_set(self._option_key, option)
