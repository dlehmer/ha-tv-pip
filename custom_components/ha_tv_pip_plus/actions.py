"""Overlay action buttons for HA TV PiP Plus.

When a popup includes `actions`, Home Assistant mints a random one-time id for each
button and keeps the real service call here. The receiver only ever sends that id back
(authenticated with its pairing token), so a receiver can never choose what runs.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ACTION_CALLBACK_PATH = f"/api/{DOMAIN}/action"
DATA_PENDING_ACTIONS = "pending_overlay_actions"
DATA_ACTION_VIEW_REGISTERED = "overlay_action_view_registered"
DEFAULT_ACTION_TTL_SECONDS = 30
ACTION_TTL_GRACE_SECONDS = 15
MAX_ACTIONS = 3

ATTR_ACTIONS = "actions"

# Camera -> door pairings, shared by every receiver.
DATA_CAMERA_BUTTONS = "camera_buttons"
DATA_CAMERA_BUTTONS_STORE = "camera_buttons_store"
CAMERA_BUTTONS_STORAGE_KEY = f"{DOMAIN}.camera_buttons"
CAMERA_BUTTONS_STORAGE_VERSION = 1
MAX_CAMERA_BUTTONS = 4
BUTTON_TARGET_DOMAINS = ("lock", "cover", "script", "button")


@dataclass(frozen=True)
class PendingAction:
    """A service call waiting for a receiver button press."""

    device_id: str
    label: str
    domain: str
    service: str
    service_data: dict[str, Any]
    expires_at: float


@dataclass
class PendingActionStore:
    """In-memory, per-Home Assistant store of one-time action ids."""

    actions: dict[str, PendingAction] = field(default_factory=dict)

    def replace_for_device(
        self,
        device_id: str,
        specs: list[dict[str, Any]],
        *,
        ttl_seconds: int,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """Drop earlier actions for a receiver, store new ones, return wire payload."""

        now = time.monotonic() if now is None else now
        self.prune(now)
        self.clear_device(device_id)
        wire: list[dict[str, Any]] = []
        for spec in specs:
            domain, service = spec["action"].split(".", 1)
            service_data = dict(spec.get("data") or {})
            service_data.update(spec.get("target") or {})
            action_id = secrets.token_urlsafe(24)
            self.actions[action_id] = PendingAction(
                device_id=device_id,
                label=spec["label"],
                domain=domain,
                service=service,
                service_data=service_data,
                expires_at=now + ttl_seconds + ACTION_TTL_GRACE_SECONDS,
            )
            item: dict[str, Any] = {
                "id": action_id,
                "label": spec["label"],
                "confirm": bool(spec.get("confirm", True)),
            }
            if spec.get("icon"):
                item["icon"] = spec["icon"]
            if spec.get("confirm_label"):
                item["confirmLabel"] = spec["confirm_label"]
            wire.append(item)
        return wire

    def take(
        self,
        action_id: str,
        device_id: str,
        *,
        now: float | None = None,
    ) -> PendingAction | None:
        """Consume an action id. One press uses up every button on that popup."""

        now = time.monotonic() if now is None else now
        self.prune(now)
        action = self.actions.get(action_id)
        if action is None or action.device_id != device_id:
            return None
        self.clear_device(device_id)
        return action

    def clear_device(self, device_id: str) -> None:
        for action_id in [
            key for key, value in self.actions.items() if value.device_id == device_id
        ]:
            self.actions.pop(action_id, None)

    def prune(self, now: float) -> None:
        for action_id in [
            key for key, value in self.actions.items() if value.expires_at <= now
        ]:
            self.actions.pop(action_id, None)


def pending_action_store(hass: Any) -> PendingActionStore:
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get(DATA_PENDING_ACTIONS)
    if not isinstance(store, PendingActionStore):
        store = PendingActionStore()
        domain_data[DATA_PENDING_ACTIONS] = store
    return store


def camera_buttons(hass: Any) -> dict[str, dict[str, str]]:
    """Return the configured camera -> door pairings."""

    return dict(hass.data.setdefault(DOMAIN, {}).get(DATA_CAMERA_BUTTONS) or {})


def normalize_camera_buttons(raw: Any) -> dict[str, dict[str, str]]:
    """Keep only well-formed pairings: camera.* -> lock/cover/script/button."""

    result: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return result
    for camera_entity, value in raw.items():
        if not isinstance(value, dict):
            continue
        camera = str(camera_entity).strip()
        target = str(value.get("entity_id", "")).strip()
        if not camera.startswith("camera.") or target.split(".", 1)[0] not in (
            BUTTON_TARGET_DOMAINS
        ):
            continue
        entry = {"entity_id": target}
        label = str(value.get("label", "") or "").strip()[:40]
        if label:
            entry["label"] = label
        result[camera] = entry
        if len(result) >= MAX_CAMERA_BUTTONS:
            break
    return result


def _storage_store(hass: Any) -> Any:
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get(DATA_CAMERA_BUTTONS_STORE)
    if store is None:
        from homeassistant.helpers.storage import (  # type: ignore[import-not-found]
            Store,
        )

        store = Store(hass, CAMERA_BUTTONS_STORAGE_VERSION, CAMERA_BUTTONS_STORAGE_KEY)
        domain_data[DATA_CAMERA_BUTTONS_STORE] = store
    return store


async def async_load_camera_buttons(hass: Any) -> None:
    """Load pairings from .storage once per Home Assistant start."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if DATA_CAMERA_BUTTONS in domain_data:
        return
    try:
        stored = await _storage_store(hass).async_load()
    except ModuleNotFoundError:
        stored = None
    domain_data[DATA_CAMERA_BUTTONS] = normalize_camera_buttons(stored or {})


async def async_save_camera_buttons(hass: Any, raw: Any) -> dict[str, dict[str, str]]:
    """Replace the pairings and persist them."""

    pairings = normalize_camera_buttons(raw)
    hass.data.setdefault(DOMAIN, {})[DATA_CAMERA_BUTTONS] = pairings
    try:
        await _storage_store(hass).async_save(pairings)
    except ModuleNotFoundError:
        pass
    return pairings


def default_action_spec(hass: Any, camera_entity: str | None) -> dict[str, Any] | None:
    """Build the button for a camera from its configured pairing, if any."""

    if not camera_entity:
        return None
    pairing = camera_buttons(hass).get(camera_entity)
    if pairing is None:
        return None
    target = pairing["entity_id"]
    domain, _, object_id = target.partition(".")
    state = hass.states.get(target) if getattr(hass, "states", None) else None
    attributes = getattr(state, "attributes", {}) or {}
    name = str(attributes.get("friendly_name") or object_id.replace("_", " ").title())
    device_class = str(attributes.get("device_class") or "")

    spec: dict[str, Any]
    if domain == "lock":
        spec = {"action": "lock.unlock", "target": {"entity_id": [target]}}
        label, icon = f"Unlock {name}", "unlock"
    elif domain == "cover":
        spec = {"action": "cover.open_cover", "target": {"entity_id": [target]}}
        label = f"Open {name}"
        icon = "garage" if device_class == "garage" else "door"
    elif domain == "script":
        # Call the script directly so Home Assistant waits for it to finish and the
        # TV can show whether it worked.
        spec = {"action": f"script.{object_id}"}
        label, icon = name, "door"
    elif domain == "button":
        spec = {"action": "button.press", "target": {"entity_id": [target]}}
        label, icon = name, "door"
    else:
        return None

    spec["label"] = (pairing.get("label") or label)[:40]
    spec["icon"] = icon
    spec["confirm"] = True
    return spec


def actions_schema(vol: Any, cv: Any) -> Any:
    """Voluptuous schema for the `actions` service field.

    Uses only basic voluptuous building blocks so the lightweight test stubs work.
    """

    def _invalid(message: str) -> Exception:
        return getattr(vol, "Invalid", ValueError)(message)

    def _service_name(value: Any) -> str:
        text = str(value).strip()
        domain, _, service = text.partition(".")
        if not domain or not service:
            raise _invalid("action must look like `domain.service`, e.g. lock.unlock")
        return text

    def _short_text(value: Any) -> str:
        text = str(value).strip()
        if not text or len(text) > 40:
            raise _invalid("button labels must be 1-40 characters")
        return text

    def _entity_ids(value: Any) -> list[str]:
        values = value if isinstance(value, list) else str(value).split(",")
        return [cv.entity_id(str(item).strip()) for item in values if str(item).strip()]

    def _ensure_list(value: Any) -> list[Any]:
        if value is None:
            return []
        items = value if isinstance(value, list) else [value]
        if len(items) > MAX_ACTIONS:
            raise _invalid(f"at most {MAX_ACTIONS} overlay buttons are supported")
        return items

    item = vol.Schema(
        {
            vol.Required("label"): _short_text,
            vol.Required("action"): _service_name,
            vol.Optional("target"): vol.Schema(
                {
                    vol.Optional("entity_id"): _entity_ids,
                    vol.Optional("device_id"): vol.Any(str, [str]),
                    vol.Optional("area_id"): vol.Any(str, [str]),
                }
            ),
            vol.Optional("data"): dict,
            vol.Optional("icon"): str,
            vol.Optional("confirm", default=True): bool,
            vol.Optional("confirm_label"): _short_text,
        }
    )
    return vol.All(_ensure_list, [item])


def async_register_action_view(hass: Any) -> None:
    """Register the receiver callback endpoint once."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(DATA_ACTION_VIEW_REGISTERED):
        return
    http = getattr(hass, "http", None)
    if http is None:
        return
    http.register_view(_build_action_view())
    domain_data[DATA_ACTION_VIEW_REGISTERED] = True


def _build_action_view() -> Any:
    from homeassistant.components.http import (  # type: ignore[import-not-found]
        HomeAssistantView,
    )

    from .remote import _token_matches_config_entry

    class HaTvPipActionView(HomeAssistantView):  # type: ignore[misc]
        """Receives button presses from paired receivers."""

        url = ACTION_CALLBACK_PATH
        name = f"api:{DOMAIN}:action"
        # The receiver authenticates with its pairing token, not a HA user token.
        requires_auth = False

        async def post(self, request: Any) -> Any:
            hass = request.app["hass"]
            auth = request.headers.get("Authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            try:
                body = await request.json()
            except ValueError:
                return self.json({"error": "invalid JSON"}, status_code=400)
            device_id = str(body.get("deviceId", "")).strip()
            action_id = str(body.get("actionId", "")).strip()
            if not _token_matches_config_entry(hass, device_id=device_id, token=token):
                _LOGGER.warning(
                    "Rejected overlay action from unpaired receiver %s", device_id
                )
                return self.json({"error": "unauthorized"}, status_code=401)

            action = pending_action_store(hass).take(action_id, device_id)
            if action is None:
                return self.json(
                    {"error": "action expired or already used"}, status_code=410
                )

            _LOGGER.info(
                "Receiver %s pressed overlay action '%s' -> %s.%s",
                device_id,
                action.label,
                action.domain,
                action.service,
            )
            try:
                await hass.services.async_call(
                    action.domain,
                    action.service,
                    action.service_data,
                    blocking=True,
                )
            except Exception as error:  # noqa: BLE001 - report any failure to the TV
                _LOGGER.error("Overlay action '%s' failed: %s", action.label, error)
                return self.json(
                    {"error": str(error) or type(error).__name__}, status_code=500
                )
            return self.json({"ok": True, "label": action.label})

    return HaTvPipActionView()
