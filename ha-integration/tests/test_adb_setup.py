"""Tests for installing and setting up the TV app over ADB."""

import pytest

from custom_components.ha_tv_pip_plus.adb_setup import (
    APP_PACKAGE,
    GRANT_OVERLAY_COMMAND,
    AdbSetupError,
    pick_release_apk,
)
from custom_components.ha_tv_pip_plus.client import receiver_status_from_payload


def test_pick_release_apk_prefers_release_build() -> None:
    url, name, tag = pick_release_apk(
        {
            "tag_name": "v1.33.0",
            "assets": [
                {"name": "app-debug.apk", "browser_download_url": "https://x/debug"},
                {"name": "app-release.apk", "browser_download_url": "https://x/rel"},
                {"name": "integration.zip", "browser_download_url": "https://x/zip"},
            ],
        }
    )
    assert (url, name, tag) == ("https://x/rel", "app-release.apk", "v1.33.0")


def test_pick_release_apk_without_apk_fails() -> None:
    with pytest.raises(AdbSetupError) as error:
        pick_release_apk({"assets": [{"name": "notes.txt"}]})
    assert error.value.code == "no_apk_in_release"


def test_error_message_explains_fix() -> None:
    error = AdbSetupError("not_reachable", "Connection refused")
    assert "USB debugging" in str(error)
    assert "Connection refused" in str(error)


def test_grant_command_targets_this_app() -> None:
    assert APP_PACKAGE == "com.dlehmer.hatvpipplus"
    assert GRANT_OVERLAY_COMMAND == (
        "appops set com.dlehmer.hatvpipplus SYSTEM_ALERT_WINDOW allow"
    )


def test_status_parses_overlay_permission() -> None:
    status = receiver_status_from_payload(
        {"overlayPermission": "not_granted", "packageName": APP_PACKAGE}
    )
    assert status.overlay_permission == "not_granted"
    assert status.package_name == APP_PACKAGE
    assert receiver_status_from_payload({}).overlay_permission is None
