"""Set up an Android TV over network ADB: install the app and grant permissions.

Home Assistant connects to the TV's ADB service on port 5555 (enabled by turning on
USB debugging in the TV's developer options). The first connection shows an
"Allow debugging?" prompt on the TV; once accepted, Home Assistant's key is trusted.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ADB_PORT = 5555
APP_PACKAGE = "com.dlehmer.hatvpipplus"
APP_MAIN_ACTIVITY = f"{APP_PACKAGE}/com.hatvpip.receiver.MainActivity"
RELEASE_REPOSITORY = "dlehmer/ha-tv-pip"
RELEASES_API = f"https://api.github.com/repos/{RELEASE_REPOSITORY}/releases/latest"
DEVICE_APK_PATH = "/data/local/tmp/ha-tv-pip-plus.apk"
ADB_KEY_FILENAME = f"{DOMAIN}_adbkey"
APK_CACHE_FILENAME = f"{DOMAIN}_app.apk"
AUTH_TIMEOUT_S = 45.0
CONNECT_TIMEOUT_S = 9.0

GRANT_OVERLAY_COMMAND = f"appops set {APP_PACKAGE} SYSTEM_ALERT_WINDOW allow"
CHECK_OVERLAY_COMMAND = f"appops get {APP_PACKAGE} SYSTEM_ALERT_WINDOW"

ERROR_HINTS = {
    "missing_library": (
        "The adb-shell library is not installed. Restart Home Assistant."
    ),
    "not_reachable": (
        "Could not reach the TV over ADB on port 5555. On the TV, open "
        "Settings > System > Developer options and turn on USB debugging "
        "(or Network debugging), then try again."
    ),
    "auth_timeout": (
        "The TV did not answer the 'Allow debugging?' prompt in time. Accept it on "
        "the TV (tick 'Always allow from this computer'), then try again."
    ),
    "auth_rejected": (
        "The TV rejected Home Assistant's debugging request. Try again and choose "
        "Allow on the TV."
    ),
    "download_failed": "Could not download the TV app from GitHub Releases.",
    "no_apk_in_release": "The latest GitHub release has no APK attached.",
    "install_conflict": (
        "A differently signed copy of HA TV PiP Plus is already on the TV. "
        "Uninstall it on the TV (Settings > Apps), then try again."
    ),
    "install_failed": "The TV refused to install the app.",
    "grant_failed": "The TV did not accept the overlay permission.",
    "adb_failed": "The ADB command failed.",
}


class AdbSetupError(Exception):
    """A TV setup step failed; `code` keys into ERROR_HINTS and translations."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        message = ERROR_HINTS.get(code, code)
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


@dataclass(frozen=True)
class InstallResult:
    """What an install did, for logging and the UI."""

    apk_name: str
    release: str
    overlay_granted: bool


async def async_grant_overlay_permission(hass: Any, host: str) -> None:
    """Grant the app permission to draw over other apps."""

    device = await _async_connect(hass, host)
    try:
        await _async_grant_overlay(device)
    finally:
        await _async_close(device)


async def async_install_app(hass: Any, host: str) -> InstallResult:
    """Download the latest app release, install it, grant overlay, and launch it."""

    apk_url, apk_name, release = await _async_latest_apk(hass)
    apk_path = hass.config.path(".storage", APK_CACHE_FILENAME)
    await _async_download(hass, apk_url, apk_path)

    device = await _async_connect(hass, host)
    try:
        await _async_adb(device.push(apk_path, DEVICE_APK_PATH, read_timeout_s=180.0))
        output = str(
            await _async_adb(
                device.shell(
                    f"pm install -r -g {DEVICE_APK_PATH}",
                    read_timeout_s=240.0,
                    timeout_s=300.0,
                )
            )
        )
        await _async_adb(device.shell(f"rm -f {DEVICE_APK_PATH}"))
        if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in output:
            raise AdbSetupError("install_conflict")
        if "Success" not in output:
            raise AdbSetupError("install_failed", output.strip()[-200:])

        overlay_granted = True
        try:
            await _async_grant_overlay(device)
        except AdbSetupError as error:
            _LOGGER.warning(
                "Installed the TV app but could not grant overlay: %s", error
            )
            overlay_granted = False

        await _async_adb(device.shell(f"am start -n {APP_MAIN_ACTIVITY}"))
    finally:
        await _async_close(device)

    _LOGGER.info("Installed %s (%s) on %s", apk_name, release, host)
    return InstallResult(
        apk_name=apk_name, release=release, overlay_granted=overlay_granted
    )


async def _async_grant_overlay(device: Any) -> None:
    await _async_adb(device.shell(GRANT_OVERLAY_COMMAND))
    output = str(await _async_adb(device.shell(CHECK_OVERLAY_COMMAND)))
    if "allow" not in output.lower():
        raise AdbSetupError("grant_failed", output.strip()[-200:])


async def _async_connect(hass: Any, host: str) -> Any:
    try:
        from adb_shell.adb_device_async import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
            AdbDeviceTcpAsync,
        )
        from adb_shell.exceptions import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
            DeviceAuthError,
        )
    except ModuleNotFoundError as error:
        raise AdbSetupError("missing_library") from error

    signer = await hass.async_add_executor_job(_load_signer, hass)
    device = AdbDeviceTcpAsync(
        host, ADB_PORT, default_transport_timeout_s=CONNECT_TIMEOUT_S
    )
    try:
        await device.connect(rsa_keys=[signer], auth_timeout_s=AUTH_TIMEOUT_S)
    except DeviceAuthError as error:
        raise AdbSetupError("auth_rejected") from error
    except TimeoutError as error:
        raise AdbSetupError("auth_timeout") from error
    except OSError as error:
        raise AdbSetupError("not_reachable", str(error)) from error
    except Exception as error:  # noqa: BLE001 - adb-shell raises many types
        if "timeout" in type(error).__name__.lower():
            raise AdbSetupError("auth_timeout") from error
        raise AdbSetupError("not_reachable", str(error)) from error
    return device


async def _async_adb(awaitable: Any) -> Any:
    try:
        return await awaitable
    except AdbSetupError:
        raise
    except Exception as error:  # noqa: BLE001
        raise AdbSetupError("adb_failed", str(error) or type(error).__name__) from error


async def _async_close(device: Any) -> None:
    try:
        await device.close()
    except Exception:  # noqa: BLE001
        pass


def _load_signer(hass: Any) -> Any:
    """Load (or create once) Home Assistant's ADB key for this integration."""

    from adb_shell.auth.keygen import keygen  # type: ignore[import-not-found,import-untyped,unused-ignore]
    from adb_shell.auth.sign_pythonrsa import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
        PythonRSASigner,
    )

    key_path = hass.config.path(".storage", ADB_KEY_FILENAME)
    if not os.path.isfile(key_path):
        keygen(key_path)
    with open(key_path, encoding="utf-8") as private_file:
        private_key = private_file.read()
    with open(f"{key_path}.pub", encoding="utf-8") as public_file:
        public_key = public_file.read()
    return PythonRSASigner(public_key, private_key)


async def _async_latest_apk(hass: Any) -> tuple[str, str, str]:
    session = _client_session(hass)
    try:
        async with session.get(
            RELEASES_API,
            headers={"Accept": "application/vnd.github+json"},
            timeout=_timeout(30),
        ) as response:
            if response.status != 200:
                raise AdbSetupError("download_failed", f"HTTP {response.status}")
            release = await response.json()
    except AdbSetupError:
        raise
    except Exception as error:  # noqa: BLE001
        raise AdbSetupError("download_failed", str(error)) from error
    return pick_release_apk(release)


def pick_release_apk(release: dict[str, Any]) -> tuple[str, str, str]:
    """Choose the APK asset from a GitHub release (release build preferred)."""

    assets = [
        asset
        for asset in release.get("assets") or []
        if str(asset.get("name", "")).lower().endswith(".apk")
    ]
    if not assets:
        raise AdbSetupError("no_apk_in_release")
    assets.sort(key=lambda asset: "debug" in str(asset.get("name", "")).lower())
    asset = assets[0]
    return (
        str(asset["browser_download_url"]),
        str(asset["name"]),
        str(release.get("tag_name") or release.get("name") or "latest"),
    )


async def _async_download(hass: Any, url: str, path: str) -> None:
    session = _client_session(hass)
    try:
        async with session.get(url, timeout=_timeout(300)) as response:
            if response.status != 200:
                raise AdbSetupError("download_failed", f"HTTP {response.status}")
            data = await response.read()
    except AdbSetupError:
        raise
    except Exception as error:  # noqa: BLE001
        raise AdbSetupError("download_failed", str(error)) from error
    await hass.async_add_executor_job(_write_file, path, data)


def _write_file(path: str, data: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(data)


def _client_session(hass: Any) -> Any:
    from homeassistant.helpers.aiohttp_client import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
        async_get_clientsession,
    )

    return async_get_clientsession(hass)


def _timeout(seconds: int) -> Any:
    import aiohttp  # type: ignore[import-not-found,import-untyped,unused-ignore]

    return aiohttp.ClientTimeout(total=seconds)
