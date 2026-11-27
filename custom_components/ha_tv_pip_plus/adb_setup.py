"""Set up an Android TV over network ADB: install the app and grant permissions.

Home Assistant connects to the TV's ADB service on port 5555 (enabled by turning on
USB debugging in the TV's developer options). The first connection shows an
"Allow debugging?" prompt on the TV; once accepted, Home Assistant's key is trusted.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shlex
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
AUTH_TIMEOUT_S = 45.0
CONNECT_TIMEOUT_S = 9.0

ADB_PAIR_ACTION = "com.dlehmer.hatvpipplus.action.ADB_PAIR"
ADB_PAIR_RECEIVER = f"{APP_PACKAGE}/com.hatvpip.receiver.AdbPairingReceiver"
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
    "pairing_rejected": "The TV app did not accept automatic pairing.",
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


DATA_INSTALL_LOCK = "install_lock"


def _install_lock(hass: Any) -> asyncio.Lock:
    domain_data = hass.data.setdefault(DOMAIN, {})
    lock = domain_data.get(DATA_INSTALL_LOCK)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        domain_data[DATA_INSTALL_LOCK] = lock
    return lock


async def async_install_app(hass: Any, host: str) -> InstallResult:
    """Download the latest app release, install it, grant overlay, and launch it.

    One TV at a time: the download is shared and ADB pushes are slow.
    """

    async with _install_lock(hass):
        try:
            return await _async_install_app(hass, host)
        except AdbSetupError as error:
            _LOGGER.warning("Installing the TV app on %s failed: %s", host, error)
            raise


async def _async_install_app(hass: Any, host: str) -> InstallResult:
    apk_url, apk_name, release = await _async_latest_apk(hass)
    # One file per release, downloaded once and reused for every TV.
    safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in apk_name)
    apk_path = hass.config.path(".storage", f"{DOMAIN}_{safe_name}")
    if not await hass.async_add_executor_job(os.path.isfile, apk_path):
        partial = f"{apk_path}.part"
        await _async_download(hass, apk_url, partial)
        await hass.async_add_executor_job(os.replace, partial, apk_path)

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


def new_pairing_code() -> str:
    """A random six-digit pairing code, the same shape the TV generates."""

    return str(100_000 + secrets.randbelow(900_000))


def adb_pairing_command(code: str, client_id: str, client_name: str) -> str:
    """Shell command that hands a pairing code to the TV app over ADB."""

    return " ".join(
        [
            "am broadcast",
            "-a",
            ADB_PAIR_ACTION,
            "-n",
            ADB_PAIR_RECEIVER,
            "--es code",
            shlex.quote(code),
            "--es clientId",
            shlex.quote(client_id),
            "--es clientName",
            shlex.quote(client_name),
        ]
    )


async def async_prepare_adb_pairing(
    hass: Any,
    host: str,
    *,
    code: str,
    client_id: str,
    client_name: str,
) -> None:
    """Tell the TV app to expect `code` for the next pairing confirmation."""

    device = await _async_connect(hass, host)
    try:
        output = str(
            await _async_adb(
                device.shell(adb_pairing_command(code, client_id, client_name))
            )
        )
    finally:
        await _async_close(device)
    # The receiver answers RESULT_OK (-1) when it accepted the code.
    if "result=-1" not in output:
        raise AdbSetupError("pairing_rejected", output.strip()[-200:])


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


DATA_LATEST_RELEASE = "latest_release"
RELEASE_CACHE_SECONDS = 60 * 60


async def async_latest_release(hass: Any, *, force: bool = False) -> dict[str, Any]:
    """The newest GitHub release (cached for an hour)."""

    import time

    domain_data = hass.data.setdefault(DOMAIN, {})
    cached = domain_data.get(DATA_LATEST_RELEASE)
    now = time.monotonic()
    if not force and cached and now - cached[0] < RELEASE_CACHE_SECONDS:
        return dict(cached[1])

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
    domain_data[DATA_LATEST_RELEASE] = (now, release)
    return dict(release)


def release_version(release: dict[str, Any]) -> str:
    """'v1.36.0' -> '1.36.0'."""

    return str(release.get("tag_name") or release.get("name") or "").strip().lstrip("v")


async def async_download(hass: Any, url: str, path: str) -> None:
    """Download `url` to `path` with Home Assistant's HTTP session."""

    await _async_download(hass, url, path)


async def _async_latest_apk(hass: Any) -> tuple[str, str, str]:
    return pick_release_apk(await async_latest_release(hass, force=True))


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
