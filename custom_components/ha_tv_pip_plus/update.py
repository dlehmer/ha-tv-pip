"""Update entities: the TV app on each receiver, and this integration itself.

Both read the newest release from GitHub. The TV app installs over network ADB
(USB debugging on the TV). The integration downloads its release zip and swaps
its own folder in custom_components, then asks for a Home Assistant restart.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import shutil
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .adb_setup import (
    AdbSetupError,
    async_install_app,
    async_latest_release,
    release_version,
)
from .appearance import popup_appearance
from .client import ReceiverClientError
from .const import DOMAIN
from .entity import ReceiverEntity
from .version import integration_version

if TYPE_CHECKING:

    class UpdateEntity:
        """Fallback base for unit tests outside Home Assistant."""

    class UpdateEntityFeature:
        """Fallback feature flags for unit tests outside Home Assistant."""

        INSTALL = 1
        RELEASE_NOTES = 16

    class HomeAssistantError(Exception):
        """Fallback Home Assistant error for unit tests."""


else:
    try:
        from homeassistant.components.update import (
            UpdateEntity,
            UpdateEntityFeature,
        )
        from homeassistant.exceptions import HomeAssistantError
    except ModuleNotFoundError:

        class UpdateEntity:
            """Fallback base for unit tests outside Home Assistant."""

        class UpdateEntityFeature:
            """Fallback feature flags for unit tests outside Home Assistant."""

            INSTALL = 1
            RELEASE_NOTES = 16

        class HomeAssistantError(Exception):
            """Fallback Home Assistant error for unit tests."""


_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=1)
AUTO_INSTALL_WAIT_S = 120
INTEGRATION_ZIP_NAME = "ha-tv-pip-plus-integration.zip"
DATA_INTEGRATION_UPDATE_ENTRY = "integration_update_entry"
RESTART_ISSUE_ID = "restart_required_after_update"


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    """One TV-app update per receiver, plus one integration update overall."""

    entities: list[Any] = [ReceiverAppUpdate(hass, entry)]
    domain_data = hass.data.setdefault(DOMAIN, {})
    owner = domain_data.get(DATA_INTEGRATION_UPDATE_ENTRY)
    if owner is None or owner == entry.entry_id:
        domain_data[DATA_INTEGRATION_UPDATE_ENTRY] = entry.entry_id
        entities.append(IntegrationUpdate(hass))
    async_add_entities(entities, True)


def is_newer(latest: str | None, installed: str | None) -> bool:
    """Compare dotted versions ('1.36.0' > '1.35.1'); unknown is never newer."""

    def parts(version: str) -> tuple[int, ...]:
        numbers = []
        for piece in version.strip().lstrip("v").split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            numbers.append(int(digits or 0))
        return tuple(numbers)

    if not latest or not installed:
        return False
    try:
        return parts(latest) > parts(installed)
    except ValueError:
        return False


class _ReleaseUpdate(UpdateEntity):
    """Shared release lookup for both update entities."""

    _release: dict[str, Any]

    def _apply_release(self, release: dict[str, Any]) -> None:
        self._release = release
        self._attr_latest_version = release_version(release) or None
        self._attr_release_url = release.get("html_url")
        body = str(release.get("body") or "").strip()
        self._attr_release_summary = body[:250] or None

    async def async_release_notes(self) -> str | None:
        return str(getattr(self, "_release", {}).get("body") or "") or None


class ReceiverAppUpdate(ReceiverEntity, _ReleaseUpdate):
    """The HA TV PiP Plus app on one TV."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(entry, key="tv_app", name="TV App")
        self.hass = hass
        self._attr_title = "HA TV PiP Plus TV app"
        self._attr_supported_features = (
            UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES
        )
        self._attr_installed_version: str | None = None
        self._attr_latest_version: str | None = None
        self._auto_install_running = False
        self._release = {}

    async def async_update(self) -> None:
        from .binary_sensor import _async_status_for_entry

        try:
            status, _ = await _async_status_for_entry(
                self.hass, self.entry, self.host, self.port
            )
            self._attr_installed_version = status.version or None
        except ReceiverClientError:
            pass
        try:
            self._apply_release(await async_latest_release(self.hass))
        except AdbSetupError as error:
            _LOGGER.debug("Could not check for a new TV app: %s", error)
            return

        if (
            popup_appearance(self.hass)["auto_update_tv_apps"]
            and not self._auto_install_running
            and is_newer(self._attr_latest_version, self._attr_installed_version)
        ):
            # Run it as its own job through Home Assistant's install path, so the
            # status poll isn't held up and "installing" always clears afterwards.
            self._auto_install_running = True
            create_task = getattr(self.hass, "async_create_background_task", None)
            if callable(create_task):
                create_task(self._async_auto_install(), f"{DOMAIN} auto update")
            else:
                self.hass.async_create_task(self._async_auto_install())

    async def _async_auto_install(self) -> None:
        # The first status poll runs before Home Assistant has added the entity.
        # Installing then fails half-way and leaves "installing" stuck on, so
        # wait until the entity exists.
        for _ in range(AUTO_INSTALL_WAIT_S):
            if getattr(self, "entity_id", None) and getattr(self, "platform", None):
                break
            await asyncio.sleep(1)
        else:
            self._auto_install_running = False
            return
        try:
            install = getattr(self, "async_install_with_progress", None)
            if callable(install):
                await install(None, False)
            else:
                await self.async_install(None, False)
        except HomeAssistantError as error:
            _LOGGER.info("Automatic TV app update skipped: %s", error)
        finally:
            self._auto_install_running = False

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install the newest app over ADB. Home Assistant tracks "installing"."""

        try:
            result = await async_install_app(self.hass, self.host)
        except AdbSetupError as error:
            raise HomeAssistantError(str(error)) from error
        self._attr_installed_version = (
            release_version({"tag_name": result.release})
            or self._attr_latest_version
        )

class IntegrationUpdate(_ReleaseUpdate):
    """This Home Assistant integration: downloads and installs its own files."""

    _attr_has_entity_name = False

    def __init__(self, hass: Any) -> None:
        self.hass = hass
        self._attr_name = "HA TV PiP Plus integration"
        self._attr_unique_id = f"{DOMAIN}_integration_update"
        self._attr_title = "HA TV PiP Plus integration"
        self._attr_supported_features = (
            UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES
        )
        self._attr_installed_version = integration_version()
        self._attr_latest_version: str | None = None
        self._release = {}

    async def async_update(self) -> None:
        try:
            self._apply_release(await async_latest_release(self.hass))
        except AdbSetupError as error:
            _LOGGER.debug("Could not check for a new integration: %s", error)

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Download the release zip and replace this integration's folder."""

        release = await async_latest_release(self.hass, force=True)
        asset = next(
            (
                item
                for item in release.get("assets") or []
                if item.get("name") == INTEGRATION_ZIP_NAME
            ),
            None,
        )
        if asset is None:
            raise HomeAssistantError(
                f"The latest release has no {INTEGRATION_ZIP_NAME} to install."
            )

        from .adb_setup import _client_session, _timeout

        session = _client_session(self.hass)
        try:
            async with session.get(
                asset["browser_download_url"], timeout=_timeout(120)
            ) as response:
                if response.status != 200:
                    raise HomeAssistantError(
                        f"Download failed: HTTP {response.status}"
                    )
                data = await response.read()
        except HomeAssistantError:
            raise
        except Exception as error:  # noqa: BLE001
            raise HomeAssistantError(f"Download failed: {error}") from error

        target = Path(__file__).resolve().parent
        backup_dir = Path(self.hass.config.path(f".{DOMAIN}_backup"))
        staging_dir = Path(self.hass.config.path(f".{DOMAIN}_update"))
        await self.hass.async_add_executor_job(
            install_integration_zip, data, target, staging_dir, backup_dir
        )

        new_version = release_version(release)
        _LOGGER.info("Installed HA TV PiP Plus integration %s", new_version)
        self._attr_installed_version = new_version
        _raise_restart_issue(self.hass, new_version)


def install_integration_zip(
    data: bytes, target: Path, staging_dir: Path, backup_dir: Path
) -> None:
    """Unpack the release zip and swap it in for `target` (runs in an executor).

    The previous version is kept in `backup_dir` (outside custom_components).
    """

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if "manifest.json" not in names:
            raise HomeAssistantError("The release zip has no manifest.json.")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("domain") != DOMAIN:
            raise HomeAssistantError("The release zip is for a different integration.")
        for name in names:
            resolved = (staging_dir / name).resolve()
            if not str(resolved).startswith(str(staging_dir.resolve())):
                raise HomeAssistantError("The release zip has an unsafe path.")
        shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True)
        archive.extractall(staging_dir)

    shutil.rmtree(backup_dir, ignore_errors=True)
    shutil.move(str(target), str(backup_dir))
    try:
        shutil.move(str(staging_dir), str(target))
    except Exception:
        shutil.move(str(backup_dir), str(target))
        raise


def _raise_restart_issue(hass: Any, version: str) -> None:
    try:
        from homeassistant.helpers import (  # type: ignore[import-not-found]
            issue_registry,
        )
    except ModuleNotFoundError:
        return
    issue_registry.async_create_issue(
        hass,
        DOMAIN,
        RESTART_ISSUE_ID,
        is_fixable=False,
        is_persistent=False,
        severity=issue_registry.IssueSeverity.WARNING,
        translation_key="restart_required",
        translation_placeholders={"version": version},
    )
