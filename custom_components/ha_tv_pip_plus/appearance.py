"""Popup appearance settings shared by every receiver.

Set once from any receiver's Configure page ("Customize popup appearance") and
applied to every show_camera / show_snapshot popup, unless the automation sets
that field itself.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .const import DOMAIN

DATA_APPEARANCE = "popup_appearance"
DATA_APPEARANCE_STORE = "popup_appearance_store"
APPEARANCE_STORAGE_KEY = f"{DOMAIN}.popup_appearance"
APPEARANCE_STORAGE_VERSION = 1

TEXT_MODE_NONE = "none"
TEXT_MODE_OVER_VIDEO = "over_video"
TEXT_MODE_BELOW_VIDEO = "below_video"
TEXT_MODES = (TEXT_MODE_NONE, TEXT_MODE_OVER_VIDEO, TEXT_MODE_BELOW_VIDEO)

MEDIA_KEEP_PLAYING = "none"
MEDIA_PAUSE = "pause"
MEDIA_LOWER_VOLUME = "duck"
MEDIA_MUTE = "mute"
MEDIA_BEHAVIORS = (MEDIA_KEEP_PLAYING, MEDIA_PAUSE, MEDIA_LOWER_VOLUME, MEDIA_MUTE)

# Per-TV choice; "shared" means use the setting shared by all TVs.
CONF_TV_MEDIA_BEHAVIOR = "tv_media_behavior"
MEDIA_SHARED = "shared"
TV_MEDIA_BEHAVIORS = (MEDIA_SHARED, *MEDIA_BEHAVIORS)


def tv_media_behavior(options: Any) -> str:
    """This TV's own choice ("shared" when it follows the setting for all TVs)."""

    value = str((options or {}).get(CONF_TV_MEDIA_BEHAVIOR) or MEDIA_SHARED)
    return value if value in TV_MEDIA_BEHAVIORS else MEDIA_SHARED


def media_behavior_for(hass: Any, options: Any) -> str:
    """What playing media should do during a popup on the TV with `options`."""

    own = tv_media_behavior(options)
    if own != MEDIA_SHARED:
        return own
    return str(popup_appearance(hass)["media_behavior"])

BUTTON_SHAPE_TILE = "tile"
BUTTON_SHAPE_PILL = "pill"
BUTTON_SHAPE_SQUARE = "square"
BUTTON_SHAPES = (BUTTON_SHAPE_TILE, BUTTON_SHAPE_PILL, BUTTON_SHAPE_SQUARE)

DEFAULT_APPEARANCE: dict[str, Any] = {
    "text_mode": TEXT_MODE_NONE,
    "title_size": 24,
    "title_color": "#50BFF2",
    "message_size": 18,
    "message_color": "#FBF5F5",
    "background_color": "#0F0E0E",
    "background_opacity": 70,
    "width": 0,
    "height": 0,
    # Door buttons. Defaults match Home Assistant's dark dashboard tile cards.
    "button_shape": BUTTON_SHAPE_TILE,
    "button_background_color": "#1C1C1C",
    "button_background_opacity": 92,
    "button_text_color": "#E1E1E1",
    "button_accent_color": "#03A9F4",
    "button_text_size": 16,
    "button_corner_radius": 12,
    "button_show_icon": True,
    # Timing and playback.
    "popup_duration": 30,
    "unlocked_stay_minutes": 15,
    "media_behavior": MEDIA_KEEP_PLAYING,
    "auto_update_tv_apps": True,
}

LIMITS = {
    "title_size": (10, 48),
    "message_size": (10, 40),
    "background_opacity": (0, 100),
    "width": (0, 1600),
    "height": (0, 900),
    "button_background_opacity": (0, 100),
    "button_text_size": (10, 32),
    "button_corner_radius": (0, 40),
    "popup_duration": (5, 600),
    "unlocked_stay_minutes": (1, 120),
}


def normalize_appearance(raw: Any) -> dict[str, Any]:
    """Fill gaps with defaults and clamp numbers; drop anything unknown."""

    result = dict(DEFAULT_APPEARANCE)
    if not isinstance(raw, dict):
        return result
    mode = str(raw.get("text_mode", TEXT_MODE_NONE))
    result["text_mode"] = mode if mode in TEXT_MODES else TEXT_MODE_NONE
    for key, (low, high) in LIMITS.items():
        try:
            value = int(raw.get(key, result[key]))
        except (TypeError, ValueError):
            continue
        result[key] = min(max(value, low), high)
    shape = str(raw.get("button_shape", BUTTON_SHAPE_TILE))
    result["button_shape"] = shape if shape in BUTTON_SHAPES else BUTTON_SHAPE_TILE
    result["button_show_icon"] = bool(raw.get("button_show_icon", True))
    result["auto_update_tv_apps"] = bool(raw.get("auto_update_tv_apps", True))
    media = str(raw.get("media_behavior", MEDIA_KEEP_PLAYING))
    result["media_behavior"] = media if media in MEDIA_BEHAVIORS else MEDIA_KEEP_PLAYING
    for key in (
        "title_color",
        "message_color",
        "background_color",
        "button_background_color",
        "button_text_color",
        "button_accent_color",
    ):
        color = to_hex_color(raw.get(key))
        if color:
            result[key] = color
    # 0 means "use the built-in size"; otherwise respect the overlay minimums.
    if result["width"] and result["width"] < 240:
        result["width"] = 240
    if result["height"] and result["height"] < 120:
        result["height"] = 120
    return result


def to_hex_color(value: Any) -> str | None:
    """Accept '#RRGGBB' or an [r, g, b] list (Home Assistant's color picker)."""

    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            red, green, blue = (min(max(int(part), 0), 255) for part in value)
        except (TypeError, ValueError):
            return None
        return f"#{red:02X}{green:02X}{blue:02X}"
    text = str(value or "").strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            int(text[1:], 16)
        except ValueError:
            return None
        return text.upper()
    return None


def hex_to_rgb(color: str) -> list[int]:
    return [int(color[index : index + 2], 16) for index in (1, 3, 5)]


def button_style_payload(appearance: dict[str, Any]) -> dict[str, Any]:
    """How the TV should draw door buttons (sent with every popup)."""

    alpha = round(appearance["button_background_opacity"] * 255 / 100)
    return {
        "shape": appearance["button_shape"],
        "backgroundColor": f"#{alpha:02X}{appearance['button_background_color'][1:]}",
        "textColor": appearance["button_text_color"],
        "accentColor": appearance["button_accent_color"],
        "textSize": appearance["button_text_size"],
        "cornerRadius": appearance["button_corner_radius"],
        "showIcon": appearance["button_show_icon"],
    }


def background_argb(appearance: dict[str, Any]) -> str:
    """Background color with opacity, as the receiver's #AARRGGBB."""

    alpha = round(appearance["background_opacity"] * 255 / 100)
    return f"#{alpha:02X}{appearance['background_color'][1:]}"


def popup_appearance(hass: Any) -> dict[str, Any]:
    stored = getattr(hass, "data", {}).get(DOMAIN, {}).get(DATA_APPEARANCE)
    return normalize_appearance(stored or {})


def _storage_store(hass: Any) -> Any:
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get(DATA_APPEARANCE_STORE)
    if store is None:
        from homeassistant.helpers.storage import (  # type: ignore[import-not-found]
            Store,
        )

        store = Store(hass, APPEARANCE_STORAGE_VERSION, APPEARANCE_STORAGE_KEY)
        domain_data[DATA_APPEARANCE_STORE] = store
    return store


async def async_load_appearance(hass: Any) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if DATA_APPEARANCE in domain_data:
        return
    try:
        stored = await _storage_store(hass).async_load()
    except ModuleNotFoundError:
        stored = None
    domain_data[DATA_APPEARANCE] = normalize_appearance(stored or {})


async def async_save_appearance(hass: Any, raw: Any) -> dict[str, Any]:
    appearance = normalize_appearance(raw)
    hass.data.setdefault(DOMAIN, {})[DATA_APPEARANCE] = appearance
    try:
        await _storage_store(hass).async_save(appearance)
    except ModuleNotFoundError:
        pass
    return appearance


def apply_appearance(
    request: Any,
    data: dict[str, Any],
    appearance: dict[str, Any],
    *,
    camera_title: str,
    use_popup_duration: bool = False,
) -> Any:
    """Apply saved appearance to a show request, field by field.

    Anything the automation set explicitly wins. `text_mode` decides whether the
    popup shows text at all when the automation doesn't say (`text_overlay`).
    """

    changes: dict[str, Any] = {}
    mode = appearance["text_mode"]
    if "text_overlay" not in data:
        if mode == TEXT_MODE_NONE:
            changes["title"] = None
            changes["message"] = None
        else:
            changes["text_overlay"] = mode == TEXT_MODE_OVER_VIDEO
            if request.title is None and request.message is None:
                changes["title"] = camera_title

    for field in ("title_size", "title_color", "message_size", "message_color"):
        if field not in data:
            changes[field] = appearance[field]
    if "background_color" not in data:
        changes["background_color"] = background_argb(appearance)
    if "width" not in data and request.width is None and appearance["width"]:
        changes["width"] = appearance["width"]
    if "height" not in data and request.height is None and appearance["height"]:
        changes["height"] = appearance["height"]
    if use_popup_duration and "duration_seconds" not in data:
        changes["duration_seconds"] = appearance["popup_duration"]
    return replace(request, **changes) if changes else request
