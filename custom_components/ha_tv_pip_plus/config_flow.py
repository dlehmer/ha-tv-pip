"""Config flow for HA TV PiP Plus."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any, Protocol

import voluptuous as vol  # type: ignore[import-not-found]
from homeassistant import config_entries  # type: ignore[import-not-found]

from .actions import (
    BUTTON_TARGET_DOMAINS,
    MAX_CAMERA_BUTTONS,
    async_save_camera_buttons,
    camera_buttons,
)
from .adb_setup import (
    AdbSetupError,
    async_install_app,
    async_prepare_adb_pairing,
    new_pairing_code,
)
from .appearance import (
    BUTTON_SHAPES,
    CONF_TV_MEDIA_BEHAVIOR,
    MEDIA_BEHAVIORS,
    TEXT_MODES,
    TV_MEDIA_BEHAVIORS,
    async_save_appearance,
    hex_to_rgb,
    normalize_appearance,
    popup_appearance,
    tv_media_behavior,
)
from .client import (
    ReceiverClientError,
    async_confirm_pairing,
    async_get_receiver_status,
    async_start_pairing,
)
from .const import (
    CONF_API_VERSION,
    CONF_CAMERA_DEFAULTS,
    CONF_DEFAULT_DURATION_SECONDS,
    CONF_DEFAULT_HEIGHT,
    CONF_DEFAULT_POSITION,
    CONF_DEFAULT_SNAPSHOT_FALLBACK,
    CONF_DEFAULT_STREAM_TYPE,
    CONF_DEFAULT_WIDTH,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_INSTALL_APP,
    CONF_NAME,
    CONF_PAIRING,
    CONF_PORT,
    CONF_POWER_ENTITY,
    CONF_PREFER_REMOTE_TRANSPORT,
    CONF_REMOTE_ACCESS_TOKEN,
    CONF_REMOTE_HOME_ASSISTANT_URL,
    CONF_TOKEN,
    CONF_VERSION,
    DEFAULT_PORT,
    DOMAIN,
    NOTIFICATION_POSITIONS,
    STREAM_TYPE_AUTO,
    STREAM_TYPES,
)
from .discovery import ReceiverDiscovery, parse_discovery_properties
from .remote_setup import (
    async_sync_remote_setup_values,
    suggested_remote_home_assistant_url,
)
from .schedule import (
    CONF_DND,
    CONF_DND_END,
    CONF_DND_ENTITY,
    CONF_DND_ENTITY_STATE,
    CONF_DND_SCHEDULE,
    CONF_DND_START,
    CONF_WAKE_END,
    CONF_WAKE_MODE,
    CONF_WAKE_START,
    DND_ENTITY_STATES,
    WAKE_ALWAYS,
    WAKE_MODES,
    WAKE_NEVER,
    async_notify_options_changed,
    schedule_options,
)
from .stills import (
    CONF_STILL_EVENT_IMAGE,
    CONF_STILL_FISHEYE_CAMERAS,
    CONF_STILL_FOCUS,
    CONF_STILL_ZOOM,
    CONF_WOKEN_TV_VIEW,
    STILL_FOCUS_AREAS,
    WOKEN_TV_VIEWS,
    still_options,
)

ha_selector: Any | None
try:
    ha_selector = __import__(
        "homeassistant.helpers.selector",
        fromlist=["SelectSelector"],
    )
except (ImportError, ModuleNotFoundError):
    ha_selector = None

_LOGGER = logging.getLogger(__name__)
PAIRING_CLIENT_ID = "home-assistant"
PAIRING_CLIENT_NAME = "Home Assistant"


class ZeroconfDiscoveryInfo(Protocol):
    """Runtime shape Home Assistant passes to `async_step_zeroconf`."""

    host: str
    port: int | None
    properties: dict[str, Any]


def async_get_options_flow(config_entry: Any) -> Any:
    """Return the options flow for a configured receiver."""

    return ReceiverOptionsFlow(config_entry)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg,misc]
    """Handle a config flow for HA TV PiP Plus."""

    VERSION = 1
    _discovered_receiver: ReceiverDiscovery | None = None
    _pairing_receiver: ReceiverDiscovery | None = None
    _install_receiver: ReceiverDiscovery | None = None
    _finished_receiver: ReceiverDiscovery | None = None
    _finished_token: str | None = None

    @staticmethod
    def async_get_options_flow(config_entry: Any) -> Any:
        """Return the options flow for a configured receiver."""

        return async_get_options_flow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> Any:
        """Handle manual setup when Zeroconf discovery is unavailable."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                receiver = _receiver_from_user_input(user_input)
            except ValueError as error:
                errors["base"] = str(error)
            else:
                await self.async_set_unique_id(receiver.device_id)
                self._abort_if_unique_id_configured()
                if user_input.get(CONF_INSTALL_APP):
                    self._install_receiver = receiver
                    return await self.async_step_install_app()
                if receiver.pairing == "disabled":
                    return await self._async_finish(receiver)
                self._pairing_receiver = receiver
                return await self.async_step_pair()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                    vol.Optional(CONF_INSTALL_APP, default=False): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_install_app(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> Any:
        """Install the TV app over network ADB, then continue to pairing."""

        receiver = self._install_receiver
        if receiver is None:
            return self.async_abort(reason="invalid_discovery")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await async_install_app(self.hass, receiver.host)
            except AdbSetupError as error:
                _LOGGER.warning("HA TV PiP Plus app install failed: %s", error)
                errors["base"] = f"adb_{error.code}"
            else:
                await _async_wait_for_receiver(receiver.host, receiver.port)
                self._install_receiver = None
                token = await _async_pair_over_adb(self.hass, receiver)
                if token is not None:
                    return await self._async_finish(
                        replace(receiver, pairing="paired"), token=token
                    )
                # Automatic pairing failed: fall back to the code on the TV.
                self._pairing_receiver = receiver
                return await self.async_step_pair()

        return self.async_show_form(
            step_id="install_app",
            data_schema=vol.Schema({}),
            description_placeholders={CONF_HOST: receiver.host},
            errors=errors,
        )

    async def _async_finish(
        self, receiver: ReceiverDiscovery, *, token: str | None = None
    ) -> Any:
        """Paired: ask how this TV should be turned on, then create the entry."""

        self._finished_receiver = receiver
        self._finished_token = token
        return await self.async_step_turn_tv_on()

    async def async_step_turn_tv_on(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> Any:
        """Ask whether Home Assistant should turn this TV on for popups.

        Turning a TV on needs an entity that powers it, which the Android TV
        Remote integration provides. If one is already paired for this TV's
        address it is suggested; otherwise the form explains how to pair it.
        """

        receiver = self._finished_receiver
        if receiver is None:
            return self.async_abort(reason="invalid_discovery")
        hass = getattr(self, "hass", None)
        suggested = android_tv_remote_entity_for_host(hass, receiver.host)
        errors: dict[str, str] = {}
        wake_mode = WAKE_ALWAYS if suggested else WAKE_NEVER
        power_entity = suggested or ""

        if user_input is not None:
            wake_mode = str(user_input.get(CONF_WAKE_MODE) or WAKE_NEVER)
            if wake_mode not in WAKE_MODES:
                wake_mode = WAKE_NEVER
            power_entity = str(user_input.get(CONF_POWER_ENTITY) or "").strip()
            if wake_mode != WAKE_NEVER and not power_entity:
                errors[CONF_POWER_ENTITY] = "power_entity_required"
            else:
                options: dict[str, Any] = {CONF_WAKE_MODE: wake_mode}
                if power_entity:
                    options[CONF_POWER_ENTITY] = power_entity
                return _create_receiver_entry(
                    self, receiver, token=self._finished_token, options=options
                )

        return self.async_show_form(
            step_id="turn_tv_on",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_WAKE_MODE, default=wake_mode): _choice_selector(
                        (WAKE_NEVER, WAKE_ALWAYS), "wake_tv_setup"
                    ),
                    _optional_suggested(
                        CONF_POWER_ENTITY, power_entity
                    ): _entity_selector(("remote", "media_player")),
                }
            ),
            description_placeholders={
                CONF_NAME: receiver.name,
                CONF_HOST: receiver.host,
                "pair_remote_url": ANDROID_TV_REMOTE_SETUP_URL,
                "found": (
                    f"Found {suggested}, the Android TV Remote for this TV. "
                    "It's already picked below."
                    if suggested
                    else "No Android TV Remote was found for this TV yet."
                ),
            },
            errors=errors,
        )

    async def async_step_zeroconf(
        self,
        discovery_info: ZeroconfDiscoveryInfo,
    ) -> Any:
        """Handle a discovered HA TV PiP Plus receiver."""

        _LOGGER.debug(
            "HA TV PiP Plus Zeroconf flow triggered: host=%s port=%s properties=%s",
            discovery_info.host,
            discovery_info.port,
            dict(discovery_info.properties),
        )

        try:
            receiver = _receiver_from_zeroconf(discovery_info)
        except ValueError as error:
            _LOGGER.warning("HA TV PiP Plus Zeroconf discovery rejected: %s", error)
            return self.async_abort(reason="invalid_discovery")

        await self.async_set_unique_id(receiver.device_id)
        self._abort_if_unique_id_configured(
            updates=_receiver_discovery_updates(receiver)
        )

        self._discovered_receiver = receiver
        self.context["title_placeholders"] = {"name": receiver.name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> Any:
        """Ask the user to confirm a discovered HA TV PiP Plus receiver."""

        receiver = self._discovered_receiver
        if receiver is None:
            return self.async_abort(reason="invalid_discovery")

        if user_input is not None:
            confirmed_receiver = replace(
                receiver,
                name=_confirmed_receiver_name(user_input, fallback=receiver.name),
            )
            if confirmed_receiver.pairing == "disabled":
                return await self._async_finish(confirmed_receiver)
            self._pairing_receiver = confirmed_receiver
            return await self.async_step_pair()

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=receiver.name): str,
                }
            ),
            description_placeholders={
                CONF_HOST: receiver.host,
                CONF_NAME: receiver.name,
                CONF_PORT: str(receiver.port),
            },
            errors={},
        )

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> Any:
        """Start receiver pairing and ask the user for the TV-visible code."""

        receiver = self._pairing_receiver
        if receiver is None:
            return self.async_abort(reason="invalid_discovery")

        errors: dict[str, str] = {}
        if user_input is None:
            try:
                await async_start_pairing(
                    receiver.host,
                    receiver.port,
                    client_id=PAIRING_CLIENT_ID,
                    client_name=PAIRING_CLIENT_NAME,
                    replace_existing=True,
                )
            except ReceiverClientError as error:
                _LOGGER.warning("Unable to start HA TV PiP Plus pairing: %s", error)
                errors["base"] = str(error)

        if user_input is not None:
            code = str(user_input.get("code", "")).strip()
            if not code:
                errors["code"] = "invalid_code"
            else:
                try:
                    result = await async_confirm_pairing(
                        receiver.host,
                        receiver.port,
                        client_id=PAIRING_CLIENT_ID,
                        client_name=PAIRING_CLIENT_NAME,
                        code=code,
                    )
                except ReceiverClientError as error:
                    _LOGGER.warning(
                        "Unable to confirm HA TV PiP Plus pairing: %s", error
                    )
                    errors["base"] = str(error)
                else:
                    return await self._async_finish(
                        replace(receiver, pairing="paired"), token=result.token
                    )

        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({vol.Required("code"): str}),
            description_placeholders={
                CONF_NAME: receiver.name,
                CONF_HOST: receiver.host,
                CONF_PORT: str(receiver.port),
            },
            errors=errors,
        )


SECTION_POWER = "turn_tv_on"
SECTION_DND = "do_not_disturb_settings"
SECTION_CAMERA_BUTTONS = "camera_buttons"
SECTION_APPEARANCE = "appearance"
SECTION_ADVANCED = "advanced"
CONF_SEND_TEST = "send_test_popup"
_STILL_KEYS = (
    CONF_WOKEN_TV_VIEW,
    CONF_STILL_ZOOM,
    CONF_STILL_FOCUS,
    CONF_STILL_FISHEYE_CAMERAS,
    CONF_STILL_EVENT_IMAGE,
)
CLEARABLE_FIELDS = frozenset({CONF_POWER_ENTITY, CONF_DND_ENTITY})
TEST_AFTER_SAVE_DELAY_S = 2.0


class ReceiverOptionsFlow(config_entries.OptionsFlow):  # type: ignore[misc]
    """One-page options dialog for a TV.

    This TV's own settings (stream defaults, turning the TV on, do not disturb,
    advanced) sit next to the settings shared by every TV (door buttons, popup
    look), each group in its own section of the same dialog.
    """

    def __init__(self, config_entry: Any) -> None:
        """Initialize the options flow with a Home Assistant config entry.

        Home Assistant's newer OptionsFlow exposes `self.config_entry` after the
        flow manager attaches runtime context. Keeping a local reference makes
        the flow tolerant of older Core versions that do not expose that helper.
        """

        self._config_entry = config_entry

    @property
    def _entry(self) -> Any:
        """Return the active config entry across supported Home Assistant versions."""

        try:
            return self.config_entry
        except (AttributeError, ValueError):
            return self._config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        """Show and save the whole options dialog."""

        entry = self._entry
        hass = getattr(self, "hass", None)
        errors: dict[str, str] = {}
        values = _current_option_values(entry, hass)

        if user_input is not None:
            values = _merge_option_values(values, user_input)
            options, error_field, error = _options_from_values(entry, hass, values)
            pairings: dict[str, dict[str, str]] = {}
            if error is None:
                pairings, error = camera_buttons_from_form(
                    values[SECTION_CAMERA_BUTTONS]
                )
                error_field = "base"
            if error is None and _remote_setup_changed(entry, options):
                if not await async_sync_remote_setup_values(
                    hass,
                    entry,
                    str(options.get(CONF_REMOTE_HOME_ASSISTANT_URL, "")),
                    str(options.get(CONF_REMOTE_ACCESS_TOKEN, "")),
                ):
                    error_field, error = "base", "cannot_connect"
            if error is not None:
                errors[error_field or "base"] = error
            else:
                if hass is not None:
                    if pairings != (camera_buttons(hass) or {}):
                        await async_save_camera_buttons(hass, pairings)
                    if _appearance_changed(hass, values[SECTION_APPEARANCE]):
                        await async_save_appearance(hass, values[SECTION_APPEARANCE])
                    if values.get(CONF_SEND_TEST):
                        _schedule_test_popup(hass, entry)
                    _schedule_options_notice(hass, entry)
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(_options_schema(values)),
            description_placeholders={
                "suggested_url": suggested_remote_home_assistant_url(hass)
                or "not configured"
            },
            errors=errors,
        )


ANDROID_TV_REMOTE_DOMAIN = "androidtv_remote"
ANDROID_TV_REMOTE_SETUP_URL = (
    "https://my.home-assistant.io/redirect/config_flow_start/"
    "?domain=androidtv_remote"
)


def android_tv_remote_entity_for_host(hass: Any | None, host: str) -> str | None:
    """The Android TV Remote entity already paired with the TV at `host`, if any."""

    entries_for = getattr(getattr(hass, "config_entries", None), "async_entries", None)
    if not callable(entries_for) or not host:
        return None
    entry_ids = [
        entry.entry_id
        for entry in entries_for(ANDROID_TV_REMOTE_DOMAIN)
        if str((entry.data or {}).get("host", "")).strip() == host
    ]
    if not entry_ids:
        return None
    try:
        from homeassistant.helpers import (  # type: ignore[import-not-found]
            entity_registry as er,
        )
    except (ImportError, ModuleNotFoundError):
        return None
    registry = er.async_get(hass)
    found: dict[str, str] = {}
    for entry_id in entry_ids:
        for entity in er.async_entries_for_config_entry(registry, entry_id):
            if entity.disabled_by is None:
                found.setdefault(entity.domain, entity.entity_id)
    return found.get("remote") or found.get("media_player")


def _section(fields: dict[Any, Any], *, collapsed: bool) -> Any:
    """Group fields into a (collapsible) section of the options dialog."""

    try:
        from homeassistant.data_entry_flow import (  # type: ignore[import-not-found]
            section,
        )
    except (ImportError, ModuleNotFoundError):
        return fields
    return section(vol.Schema(fields), {"collapsed": collapsed})


def _optional_suggested(key: str, value: Any) -> Any:
    """Optional field prefilled with `value` but still clearable."""

    if value:
        return vol.Optional(key, description={"suggested_value": value})
    return vol.Optional(key)


def _time_selector() -> Any:
    if ha_selector is None:
        return str
    return ha_selector.TimeSelector()


def _camera_list_selector() -> Any:
    if ha_selector is None:
        return list
    return ha_selector.EntitySelector(
        ha_selector.EntitySelectorConfig(domain=["camera"], multiple=True)
    )


def _zoom_selector() -> Any:
    if ha_selector is None:
        return float
    return ha_selector.NumberSelector(
        ha_selector.NumberSelectorConfig(
            min=1,
            max=4,
            step=0.5,
            unit_of_measurement="×",
            mode=ha_selector.NumberSelectorMode.SLIDER,
        )
    )


def _any_entity_selector() -> Any:
    if ha_selector is None:
        return str
    return ha_selector.EntitySelector(ha_selector.EntitySelectorConfig())


def _choice_selector(options: tuple[str, ...], translation_key: str) -> Any:
    if ha_selector is None:
        return vol.Any(*options)
    return ha_selector.SelectSelector(
        ha_selector.SelectSelectorConfig(
            options=list(options),
            mode=ha_selector.SelectSelectorMode.DROPDOWN,
            translation_key=translation_key,
        )
    )


def _current_option_values(entry: Any, hass: Any | None) -> dict[str, Any]:
    """Everything the options dialog shows, read from the entry and shared stores."""

    basic = _current_basic_options(entry)
    schedule = schedule_options(entry.options)
    position = str(entry.options.get(CONF_DEFAULT_POSITION, "top_right")).strip()
    if position not in NOTIFICATION_POSITIONS:
        position = "top_right"
    suggested_url = suggested_remote_home_assistant_url(hass)
    return {
        CONF_DEFAULT_STREAM_TYPE: basic[CONF_DEFAULT_STREAM_TYPE],
        CONF_DEFAULT_DURATION_SECONDS: basic.get(CONF_DEFAULT_DURATION_SECONDS, 0),
        CONF_DEFAULT_SNAPSHOT_FALLBACK: basic[CONF_DEFAULT_SNAPSHOT_FALLBACK],
        CONF_PREFER_REMOTE_TRANSPORT: basic[CONF_PREFER_REMOTE_TRANSPORT],
        CONF_TV_MEDIA_BEHAVIOR: tv_media_behavior(entry.options),
        SECTION_POWER: {
            CONF_POWER_ENTITY: basic.get(CONF_POWER_ENTITY, ""),
            CONF_WAKE_MODE: schedule[CONF_WAKE_MODE],
            CONF_WAKE_START: schedule[CONF_WAKE_START],
            CONF_WAKE_END: schedule[CONF_WAKE_END],
            **still_options(entry.options),
        },
        SECTION_DND: {
            CONF_DND: schedule[CONF_DND],
            CONF_DND_ENTITY: schedule[CONF_DND_ENTITY],
            CONF_DND_ENTITY_STATE: schedule[CONF_DND_ENTITY_STATE],
            CONF_DND_SCHEDULE: schedule[CONF_DND_SCHEDULE],
            CONF_DND_START: schedule[CONF_DND_START],
            CONF_DND_END: schedule[CONF_DND_END],
        },
        SECTION_CAMERA_BUTTONS: camera_buttons_form_values(
            camera_buttons_to_form(camera_buttons(hass) if hass else {})
        ),
        SECTION_APPEARANCE: popup_appearance(hass),
        SECTION_ADVANCED: {
            CONF_DEFAULT_POSITION: position,
            CONF_DEFAULT_WIDTH: _optional_default_int(
                entry.options.get(CONF_DEFAULT_WIDTH), minimum=0, maximum=1600
            )
            or 0,
            CONF_DEFAULT_HEIGHT: _optional_default_int(
                entry.options.get(CONF_DEFAULT_HEIGHT), minimum=0, maximum=900
            )
            or 0,
            CONF_REMOTE_HOME_ASSISTANT_URL: str(
                entry.options.get(CONF_REMOTE_HOME_ASSISTANT_URL) or suggested_url
            ).strip(),
            CONF_REMOTE_ACCESS_TOKEN: str(
                entry.options.get(CONF_REMOTE_ACCESS_TOKEN, "")
            ).strip(),
        },
        CONF_SEND_TEST: False,
    }


def _merge_option_values(
    current: dict[str, Any], user_input: dict[str, Any]
) -> dict[str, Any]:
    """Apply submitted values over the current ones.

    Home Assistant leaves a cleared optional field out of the submitted data, so
    in a submitted section the clearable fields (entity pickers, door buttons)
    are taken only from what was sent. Section
    fields sent flat (older frontends, tests) are put back into their section.
    """

    merged = {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in current.items()
    }
    sections = (
        SECTION_POWER,
        SECTION_DND,
        SECTION_CAMERA_BUTTONS,
        SECTION_APPEARANCE,
        SECTION_ADVANCED,
    )
    for key, value in user_input.items():
        if key in sections and isinstance(value, dict):
            if key == SECTION_CAMERA_BUTTONS:
                merged[key] = dict(value)
            else:
                kept = {
                    name: old
                    for name, old in merged[key].items()
                    if name not in CLEARABLE_FIELDS
                }
                merged[key] = {**kept, **value}
            continue
        for section_key in sections:
            if section_key != SECTION_CAMERA_BUTTONS and key in merged[section_key]:
                merged[section_key][key] = value
                break
        else:
            merged[key] = value
    return merged


def _options_from_values(
    entry: Any, hass: Any | None, values: dict[str, Any]
) -> tuple[dict[str, Any], str | None, str | None]:
    """Turn dialog values into this TV's options, or (…, field, error key)."""

    stream_type = str(values.get(CONF_DEFAULT_STREAM_TYPE, STREAM_TYPE_AUTO)).strip()
    if stream_type not in STREAM_TYPES:
        return {}, CONF_DEFAULT_STREAM_TYPE, "invalid_default_stream_type"
    duration = _optional_default_int(
        values.get(CONF_DEFAULT_DURATION_SECONDS), minimum=0, maximum=3600
    )
    if duration is None:
        return {}, CONF_DEFAULT_DURATION_SECONDS, "invalid_default_duration"

    power = values[SECTION_POWER]
    dnd = values[SECTION_DND]
    advanced = values[SECTION_ADVANCED]

    position = str(advanced.get(CONF_DEFAULT_POSITION) or "top_right").strip()
    if position not in NOTIFICATION_POSITIONS:
        return {}, CONF_DEFAULT_POSITION, "invalid_default_position"
    width = _optional_default_int(
        advanced.get(CONF_DEFAULT_WIDTH), minimum=0, maximum=1600
    )
    height = _optional_default_int(
        advanced.get(CONF_DEFAULT_HEIGHT), minimum=0, maximum=900
    )
    if width is None:
        return {}, CONF_DEFAULT_WIDTH, "invalid_default_overlay_size"
    if height is None:
        return {}, CONF_DEFAULT_HEIGHT, "invalid_default_overlay_size"
    raw_url = str(advanced.get(CONF_REMOTE_HOME_ASSISTANT_URL) or "").strip()
    token = str(advanced.get(CONF_REMOTE_ACCESS_TOKEN) or "").strip()
    remote_url = (raw_url or suggested_remote_home_assistant_url(hass)) if token else ""
    if bool(remote_url) != bool(token):
        return {}, "base", "remote_fields_required"

    options = _basic_options(
        default_stream_type=stream_type,
        default_duration=duration,
        default_snapshot_fallback=bool(values.get(CONF_DEFAULT_SNAPSHOT_FALLBACK)),
        prefer_remote_transport=bool(values.get(CONF_PREFER_REMOTE_TRANSPORT)),
        power_entity=str(power.get(CONF_POWER_ENTITY) or "").strip(),
    )
    options[CONF_TV_MEDIA_BEHAVIOR] = tv_media_behavior(
        {CONF_TV_MEDIA_BEHAVIOR: values.get(CONF_TV_MEDIA_BEHAVIOR)}
    )
    options[CONF_DEFAULT_POSITION] = position
    if width > 0:
        options[CONF_DEFAULT_WIDTH] = width
    if height > 0:
        options[CONF_DEFAULT_HEIGHT] = height
    if remote_url and token:
        options[CONF_REMOTE_HOME_ASSISTANT_URL] = remote_url
        options[CONF_REMOTE_ACCESS_TOKEN] = token
    options.update(
        schedule_options(
            {
                CONF_WAKE_MODE: power.get(CONF_WAKE_MODE),
                CONF_WAKE_START: power.get(CONF_WAKE_START),
                CONF_WAKE_END: power.get(CONF_WAKE_END),
                **{key: dnd.get(key) for key in dnd},
            }
        )
    )
    if not options[CONF_DND_ENTITY]:
        options.pop(CONF_DND_ENTITY)
    options.update(
        still_options({key: power.get(key) for key in _STILL_KEYS})
    )
    _preserve_camera_defaults(entry, options)
    return options, None, None


def _remote_setup_changed(entry: Any, options: dict[str, Any]) -> bool:
    """Only contact the TV when the remote receiver settings actually change."""

    return any(
        str(options.get(key, "")).strip()
        != str(entry.options.get(key, "")).strip()
        for key in (CONF_REMOTE_HOME_ASSISTANT_URL, CONF_REMOTE_ACCESS_TOKEN)
    )


def _appearance_changed(hass: Any, submitted: dict[str, Any]) -> bool:
    return bool(normalize_appearance(submitted) != popup_appearance(hass))


def _schedule_options_notice(hass: Any, entry: Any) -> None:
    """Refresh the TV's settings entities once the saved options are in place."""

    async def _notify() -> None:
        await asyncio.sleep(0.5)
        async_notify_options_changed(hass, entry.entry_id)

    create_task = getattr(hass, "async_create_task", None)
    if callable(create_task):
        create_task(_notify())


def _schedule_test_popup(hass: Any, entry: Any) -> None:
    """Send a test popup once the new options are in place."""

    async def _send() -> None:
        await asyncio.sleep(TEST_AFTER_SAVE_DELAY_S)
        from .services import async_send_test_popup

        try:
            await async_send_test_popup(hass, entry)
        except Exception as error:  # noqa: BLE001
            _LOGGER.warning("Test popup to %s failed: %s", entry.title, error)

    create_task = getattr(hass, "async_create_background_task", None)
    if callable(create_task):
        create_task(_send(), f"ha_tv_pip_plus test popup {entry.entry_id}")
    else:
        hass.async_create_task(_send())


def _options_schema(values: dict[str, Any]) -> dict[Any, Any]:
    """The whole options dialog, prefilled with `values`."""

    power = values[SECTION_POWER]
    dnd = values[SECTION_DND]
    advanced = values[SECTION_ADVANCED]
    camera_values = values[SECTION_CAMERA_BUTTONS]

    camera_fields: dict[Any, Any] = {}
    for slot in range(1, MAX_CAMERA_BUTTONS + 1):
        for key, selector in (
            (f"camera_{slot}", _entity_selector(("camera",))),
            (f"button_target_{slot}", _entity_selector(BUTTON_TARGET_DOMAINS)),
            (f"button_label_{slot}", str),
        ):
            camera_fields[_optional_suggested(key, camera_values.get(key))] = selector

    return {
        vol.Optional(
            CONF_DEFAULT_STREAM_TYPE, default=values[CONF_DEFAULT_STREAM_TYPE]
        ): _select_dropdown(STREAM_TYPES),
        vol.Optional(
            CONF_DEFAULT_DURATION_SECONDS,
            default=values[CONF_DEFAULT_DURATION_SECONDS],
        ): int,
        vol.Optional(
            CONF_DEFAULT_SNAPSHOT_FALLBACK,
            default=bool(values[CONF_DEFAULT_SNAPSHOT_FALLBACK]),
        ): bool,
        vol.Optional(
            CONF_PREFER_REMOTE_TRANSPORT,
            default=bool(values[CONF_PREFER_REMOTE_TRANSPORT]),
        ): bool,
        vol.Optional(
            CONF_TV_MEDIA_BEHAVIOR, default=values[CONF_TV_MEDIA_BEHAVIOR]
        ): _choice_selector(TV_MEDIA_BEHAVIORS, "tv_media_behavior"),
        vol.Required(SECTION_POWER): _section(
            {
                _optional_suggested(
                    CONF_POWER_ENTITY, power.get(CONF_POWER_ENTITY)
                ): _entity_selector(("media_player", "remote")),
                vol.Optional(
                    CONF_WAKE_MODE, default=power[CONF_WAKE_MODE]
                ): _choice_selector(WAKE_MODES, "wake_tv"),
                vol.Optional(
                    CONF_WAKE_START, default=power[CONF_WAKE_START]
                ): _time_selector(),
                vol.Optional(
                    CONF_WAKE_END, default=power[CONF_WAKE_END]
                ): _time_selector(),
                vol.Optional(
                    CONF_WOKEN_TV_VIEW, default=power[CONF_WOKEN_TV_VIEW]
                ): _choice_selector(WOKEN_TV_VIEWS, "woken_tv_view"),
                vol.Optional(
                    CONF_STILL_EVENT_IMAGE,
                    default=bool(power[CONF_STILL_EVENT_IMAGE]),
                ): bool,
                vol.Optional(
                    CONF_STILL_ZOOM, default=power[CONF_STILL_ZOOM]
                ): _zoom_selector(),
                vol.Optional(
                    CONF_STILL_FOCUS, default=power[CONF_STILL_FOCUS]
                ): _choice_selector(STILL_FOCUS_AREAS, "still_focus"),
                vol.Optional(
                    CONF_STILL_FISHEYE_CAMERAS,
                    default=list(power[CONF_STILL_FISHEYE_CAMERAS]),
                ): _camera_list_selector(),
            },
            collapsed=False,
        ),
        vol.Required(SECTION_DND): _section(
            {
                vol.Optional(CONF_DND, default=bool(dnd[CONF_DND])): bool,
                _optional_suggested(
                    CONF_DND_ENTITY, dnd.get(CONF_DND_ENTITY)
                ): _any_entity_selector(),
                vol.Optional(
                    CONF_DND_ENTITY_STATE, default=dnd[CONF_DND_ENTITY_STATE]
                ): _choice_selector(DND_ENTITY_STATES, "do_not_disturb_entity_state"),
                vol.Optional(
                    CONF_DND_SCHEDULE, default=bool(dnd[CONF_DND_SCHEDULE])
                ): bool,
                vol.Optional(
                    CONF_DND_START, default=dnd[CONF_DND_START]
                ): _time_selector(),
                vol.Optional(CONF_DND_END, default=dnd[CONF_DND_END]): _time_selector(),
            },
            collapsed=False,
        ),
        vol.Required(SECTION_CAMERA_BUTTONS): _section(camera_fields, collapsed=True),
        vol.Required(SECTION_APPEARANCE): _section(
            appearance_form_fields(normalize_appearance(values[SECTION_APPEARANCE])),
            collapsed=True,
        ),
        vol.Required(SECTION_ADVANCED): _section(
            {
                vol.Optional(
                    CONF_DEFAULT_POSITION, default=advanced[CONF_DEFAULT_POSITION]
                ): _select_dropdown(NOTIFICATION_POSITIONS),
                vol.Optional(
                    CONF_DEFAULT_WIDTH, default=advanced[CONF_DEFAULT_WIDTH]
                ): int,
                vol.Optional(
                    CONF_DEFAULT_HEIGHT, default=advanced[CONF_DEFAULT_HEIGHT]
                ): int,
                vol.Optional(
                    CONF_REMOTE_HOME_ASSISTANT_URL,
                    default=advanced[CONF_REMOTE_HOME_ASSISTANT_URL],
                ): str,
                vol.Optional(
                    CONF_REMOTE_ACCESS_TOKEN,
                    default=advanced[CONF_REMOTE_ACCESS_TOKEN],
                ): str,
            },
            collapsed=True,
        ),
        vol.Optional(CONF_SEND_TEST, default=False): bool,
    }


def appearance_form_fields(current: dict[str, Any]) -> dict[Any, Any]:
    """Form fields for the popup appearance page, prefilled with `current`."""

    def number(low: int, high: int, unit: str, slider: bool = False) -> Any:
        if ha_selector is None:
            return int
        return ha_selector.NumberSelector(
            ha_selector.NumberSelectorConfig(
                min=low,
                max=high,
                step=1,
                unit_of_measurement=unit,
                mode=(
                    ha_selector.NumberSelectorMode.SLIDER
                    if slider
                    else ha_selector.NumberSelectorMode.BOX
                ),
            )
        )

    def color() -> Any:
        if ha_selector is None:
            return list
        return ha_selector.ColorRGBSelector()

    def choice(options: tuple[str, ...], translation_key: str) -> Any:
        if ha_selector is None:
            return vol.Any(*options)
        return ha_selector.SelectSelector(
            ha_selector.SelectSelectorConfig(
                options=list(options),
                mode=ha_selector.SelectSelectorMode.LIST,
                translation_key=translation_key,
            )
        )

    def text_mode() -> Any:
        return choice(TEXT_MODES, "text_mode")

    return {
        vol.Required("text_mode", default=current["text_mode"]): text_mode(),
        vol.Required("title_size", default=current["title_size"]): number(
            10, 48, "px"
        ),
        vol.Required(
            "title_color", default=hex_to_rgb(current["title_color"])
        ): color(),
        vol.Required("message_size", default=current["message_size"]): number(
            10, 40, "px"
        ),
        vol.Required(
            "message_color", default=hex_to_rgb(current["message_color"])
        ): color(),
        vol.Required(
            "background_color", default=hex_to_rgb(current["background_color"])
        ): color(),
        vol.Required(
            "background_opacity", default=current["background_opacity"]
        ): number(0, 100, "%", slider=True),
        vol.Required("width", default=current["width"]): number(0, 1600, "px"),
        vol.Required("height", default=current["height"]): number(0, 900, "px"),
        vol.Required("button_shape", default=current["button_shape"]): choice(
            BUTTON_SHAPES, "button_shape"
        ),
        vol.Required(
            "button_background_color",
            default=hex_to_rgb(current["button_background_color"]),
        ): color(),
        vol.Required(
            "button_background_opacity",
            default=current["button_background_opacity"],
        ): number(0, 100, "%", slider=True),
        vol.Required(
            "button_text_color", default=hex_to_rgb(current["button_text_color"])
        ): color(),
        vol.Required(
            "button_accent_color", default=hex_to_rgb(current["button_accent_color"])
        ): color(),
        vol.Required(
            "button_text_size", default=current["button_text_size"]
        ): number(10, 32, "px"),
        vol.Required(
            "button_corner_radius", default=current["button_corner_radius"]
        ): number(0, 40, "px"),
        vol.Required("button_show_icon", default=current["button_show_icon"]): bool,
        vol.Required(
            "popup_duration", default=current["popup_duration"]
        ): number(5, 600, "s"),
        vol.Required(
            "unlocked_stay_minutes", default=current["unlocked_stay_minutes"]
        ): number(1, 120, "min"),
        vol.Required("media_behavior", default=current["media_behavior"]): choice(
            MEDIA_BEHAVIORS, "media_behavior"
        ),
        vol.Required(
            "dim_background", default=current["dim_background"]
        ): number(0, 80, "%", slider=True),
        vol.Required(
            "auto_update_tv_apps", default=current["auto_update_tv_apps"]
        ): bool,
    }


def _entity_selector(domains: tuple[str, ...]) -> Any:
    """Return an entity picker limited to the given domains."""

    if ha_selector is None:
        return str
    return ha_selector.EntitySelector(
        ha_selector.EntitySelectorConfig(domain=list(domains))
    )


def camera_buttons_to_form(pairings: dict[str, dict[str, str]]) -> dict[str, str]:
    """Flatten stored pairings into numbered form fields."""

    values: dict[str, str] = {}
    for slot, (camera, pairing) in enumerate(pairings.items(), start=1):
        if slot > MAX_CAMERA_BUTTONS:
            break
        values[f"camera_{slot}"] = camera
        values[f"button_target_{slot}"] = pairing.get("entity_id", "")
        if pairing.get("label"):
            values[f"button_label_{slot}"] = pairing["label"]
    return values


def camera_buttons_form_values(values: dict[str, Any]) -> dict[str, str]:
    return {key: str(value).strip() for key, value in values.items() if value}


def camera_buttons_from_form(
    user_input: dict[str, Any],
) -> tuple[dict[str, dict[str, str]], str | None]:
    """Turn numbered form fields into pairings, or return an error key."""

    pairings: dict[str, dict[str, str]] = {}
    for slot in range(1, MAX_CAMERA_BUTTONS + 1):
        camera = str(user_input.get(f"camera_{slot}") or "").strip()
        target = str(user_input.get(f"button_target_{slot}") or "").strip()
        label = str(user_input.get(f"button_label_{slot}") or "").strip()
        if not camera and not target:
            continue
        if not camera or not target:
            return {}, "camera_button_incomplete"
        if camera in pairings:
            return {}, "camera_button_duplicate"
        if len(label) > 40:
            return {}, "camera_button_label_too_long"
        pairing = {"entity_id": target}
        if label:
            pairing["label"] = label
        pairings[camera] = pairing
    return pairings, None


def _select_dropdown(options: tuple[str, ...]) -> Any:
    """Return a Home Assistant serializable dropdown selector for options flows."""

    if ha_selector is None:
        return vol.Any(*options)
    return ha_selector.SelectSelector(
        ha_selector.SelectSelectorConfig(
            options=list(options),
            mode=ha_selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _optional_default_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    """Return a stored numeric default, using 0 to mean no receiver default."""

    try:
        parsed_value = int(value or 0)
    except (TypeError, ValueError):
        return None
    if not minimum <= parsed_value <= maximum:
        return None
    return parsed_value


def _current_basic_options(entry: Any) -> dict[str, Any]:
    stream_type = str(
        entry.options.get(CONF_DEFAULT_STREAM_TYPE, STREAM_TYPE_AUTO)
    ).strip()
    if stream_type not in STREAM_TYPES:
        stream_type = STREAM_TYPE_AUTO
    duration = (
        _optional_default_int(
            entry.options.get(CONF_DEFAULT_DURATION_SECONDS),
            minimum=0,
            maximum=3600,
        )
        or 0
    )
    return _basic_options(
        default_stream_type=stream_type,
        default_duration=duration,
        default_snapshot_fallback=bool(
            entry.options.get(CONF_DEFAULT_SNAPSHOT_FALLBACK, True)
        ),
        prefer_remote_transport=bool(
            entry.options.get(CONF_PREFER_REMOTE_TRANSPORT, False)
        ),
        power_entity=str(entry.options.get(CONF_POWER_ENTITY) or "").strip(),
    )


def _basic_options(
    *,
    default_stream_type: str,
    default_duration: int,
    default_snapshot_fallback: bool,
    prefer_remote_transport: bool,
    power_entity: str = "",
) -> dict[str, Any]:
    options: dict[str, Any] = {
        CONF_DEFAULT_STREAM_TYPE: default_stream_type,
        CONF_DEFAULT_SNAPSHOT_FALLBACK: default_snapshot_fallback,
        CONF_PREFER_REMOTE_TRANSPORT: prefer_remote_transport,
    }
    if power_entity:
        options[CONF_POWER_ENTITY] = power_entity
    if default_duration > 0:
        options[CONF_DEFAULT_DURATION_SECONDS] = default_duration
    return options


def _preserve_camera_defaults(entry: Any, options: dict[str, Any]) -> None:
    if CONF_CAMERA_DEFAULTS in entry.options:
        options[CONF_CAMERA_DEFAULTS] = dict(entry.options[CONF_CAMERA_DEFAULTS])


def _create_receiver_entry(
    flow: ConfigFlow,
    receiver: ReceiverDiscovery,
    *,
    token: str | None = None,
    options: dict[str, Any] | None = None,
) -> Any:
    """Create a Home Assistant config entry from receiver details."""

    data = {
        CONF_DEVICE_ID: receiver.device_id,
        CONF_HOST: receiver.host,
        CONF_PORT: receiver.port,
        CONF_NAME: receiver.name,
        CONF_VERSION: receiver.version,
        CONF_PAIRING: receiver.pairing,
        CONF_API_VERSION: receiver.api_version,
    }
    if token is not None:
        data[CONF_TOKEN] = token

    flow.context["title_placeholders"] = {"name": receiver.name}
    if options:
        return flow.async_create_entry(
            title=receiver.name,
            data=data,
            options=options,
        )
    return flow.async_create_entry(
        title=receiver.name,
        data=data,
    )


def _receiver_discovery_updates(receiver: ReceiverDiscovery) -> dict[str, Any]:
    """Return mutable config-entry fields refreshed by Zeroconf discovery.

    Home Assistant calls `_abort_if_unique_id_configured` for already configured
    receivers. Passing these updates repairs DHCP address changes by stable
    receiver id without requiring the user to delete and re-add the device.
    """

    return {
        CONF_HOST: receiver.host,
        CONF_PORT: receiver.port,
        CONF_NAME: receiver.name,
        CONF_VERSION: receiver.version,
        CONF_PAIRING: receiver.pairing,
        CONF_API_VERSION: receiver.api_version,
    }


def _receiver_from_zeroconf(discovery_info: ZeroconfDiscoveryInfo) -> ReceiverDiscovery:
    """Convert Home Assistant Zeroconf data into receiver discovery data."""

    return parse_discovery_properties(
        host=discovery_info.host,
        port=discovery_info.port,
        properties=dict(discovery_info.properties),
    )


async def _async_pair_over_adb(hass: Any, receiver: ReceiverDiscovery) -> str | None:
    """Pair without a code on screen: hand the TV a code over ADB, then confirm.

    Returns the pairing token, or None when the TV or app can't do it (for
    example an older app), so the caller can fall back to manual pairing.
    """

    code = new_pairing_code()
    try:
        await async_prepare_adb_pairing(
            hass,
            receiver.host,
            code=code,
            client_id=PAIRING_CLIENT_ID,
            client_name=PAIRING_CLIENT_NAME,
        )
        result = await async_confirm_pairing(
            receiver.host,
            receiver.port,
            client_id=PAIRING_CLIENT_ID,
            client_name=PAIRING_CLIENT_NAME,
            code=code,
        )
    except (AdbSetupError, ReceiverClientError) as error:
        _LOGGER.info("Automatic pairing over ADB unavailable: %s", error)
        return None
    return str(result.token)


async def _async_wait_for_receiver(
    host: str,
    port: int,
    *,
    attempts: int = 20,
    delay_s: float = 1.5,
) -> bool:
    """Wait for a freshly installed app to start answering on its control port."""

    import asyncio

    for _ in range(attempts):
        try:
            await async_get_receiver_status(host, port)
        except ReceiverClientError:
            await asyncio.sleep(delay_s)
        else:
            return True
    return False


def _receiver_from_user_input(user_input: dict[str, Any]) -> ReceiverDiscovery:
    """Convert manual setup data into receiver discovery data."""

    host = str(user_input.get(CONF_HOST, "")).strip()
    if not host:
        raise ValueError("invalid_host")

    port = _manual_port(user_input.get(CONF_PORT, DEFAULT_PORT))
    return ReceiverDiscovery(
        device_id=f"manual-{host}-{port}",
        name=f"HA TV PiP Plus Receiver ({host})",
        host=host,
        port=port,
        version="unknown",
        pairing="required",
        api_version=1,
    )


def _confirmed_receiver_name(user_input: dict[str, Any], *, fallback: str) -> str:
    """Return the receiver name confirmed by the user."""

    name = str(user_input.get(CONF_NAME, "")).strip()
    return name or fallback


def _manual_port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError("invalid_port") from None

    if port < 1 or port > 65535:
        raise ValueError("invalid_port")

    return port
