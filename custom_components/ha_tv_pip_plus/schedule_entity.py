"""Shared base for the per-TV do-not-disturb and turn-on schedule entities.

These entities read and write this TV's config entry options (see schedule.py),
so the options dialog and the entities always show the same settings.
"""

from __future__ import annotations

from typing import Any

from .entity import ReceiverEntity
from .schedule import async_update_schedule_option, schedule_options


class ReceiverScheduleEntity(ReceiverEntity):
    """Entity backed by one schedule setting in the TV's options."""

    _attr_should_poll = False

    def __init__(
        self, hass: Any, entry: Any, *, key: str, name: str, icon: str | None = None
    ) -> None:
        super().__init__(entry, key=key, name=name)
        self.hass = hass
        if icon:
            self._attr_icon = icon

    @property
    def settings(self) -> dict[str, Any]:
        return schedule_options(self.entry.options)

    async def async_added_to_hass(self) -> None:
        parent = getattr(super(), "async_added_to_hass", None)
        if callable(parent):
            await parent()
        add_listener = getattr(self.entry, "add_update_listener", None)
        on_remove = getattr(self, "async_on_remove", None)
        if callable(add_listener) and callable(on_remove):
            on_remove(add_listener(self._async_options_changed))

    async def _async_options_changed(self, hass: Any, entry: Any) -> None:
        self.entry = entry
        self._settings_changed()
        write_state = getattr(self, "async_write_ha_state", None)
        if callable(write_state):
            write_state()

    def _settings_changed(self) -> None:
        """Hook for subclasses that cache something derived from the options."""

    def _write_state(self) -> None:
        write_state = getattr(self, "async_write_ha_state", None)
        if callable(write_state):
            write_state()

    def _on_remove(self, callback: Any) -> None:
        on_remove = getattr(self, "async_on_remove", None)
        if callable(on_remove):
            on_remove(callback)

    async def _async_set(self, key: str, value: Any) -> None:
        await async_update_schedule_option(self.hass, self.entry, key, value)
