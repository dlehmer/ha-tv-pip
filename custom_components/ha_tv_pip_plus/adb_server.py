"""Wireless debugging fallback: reach the TV through our ADB server app.

Newer Google TV devices don't open ADB on port 5555 when USB debugging is on; they
only offer Wireless debugging, which needs a pairing code and an encrypted
connection that adb-shell can't do. On Home Assistant OS we install a small app
(`ha_tv_pip_adb`, shipped in this repository) that runs Google's adb server, and
talk to that server with the plain adb server protocol: it pairs with the TV,
holds the encrypted connection, and runs our shell and push commands.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import struct
import time
from typing import Any

from .adb_setup import AdbSetupError

_LOGGER = logging.getLogger(__name__)

APP_SLUG = "ha_tv_pip_adb"
APP_REPOSITORY = "https://github.com/dlehmer/ha-tv-pip"
ADB_SERVER_PORT = 5037
SERVER_CONNECT_TIMEOUT_S = 10.0
APP_START_TIMEOUT_S = 90.0

PAIRING_SERVICE = "_adb-tls-pairing._tcp.local."
CONNECT_SERVICE = "_adb-tls-connect._tcp.local."
DISCOVERY_TIMEOUT_S = 4.0

SYNC_CHUNK = 64 * 1024
PUSH_FILE_MODE = 0o100644


# --- The ADB server app (Supervisor) ---------------------------------------


def supervisor_available(hass: Any) -> bool:
    """Whether this Home Assistant can install apps (HA OS or Supervised)."""

    return "hassio" in getattr(hass.config, "components", set())


def _supervisor(hass: Any) -> Any:
    from homeassistant.components.hassio import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
        get_supervisor_client,
    )

    return get_supervisor_client(hass)


def is_server_app_slug(slug: str) -> bool:
    """Store slugs are '<repository hash>_<app slug>' for third-party apps."""

    return slug == APP_SLUG or slug.endswith(f"_{APP_SLUG}")


async def _async_find_store_app(client: Any) -> Any | None:
    for app in await client.store.addons_list():
        if is_server_app_slug(str(app.slug)):
            return app
    return None


async def async_server_app_state(hass: Any) -> str:
    """'unavailable' (no Supervisor), 'missing', 'stopped' or 'started'."""

    if not supervisor_available(hass):
        return "unavailable"
    try:
        client = _supervisor(hass)
        app = await _async_find_store_app(client)
        if app is None or not app.installed:
            return "missing"
        info = await client.addons.addon_info(app.slug)
    except Exception as error:  # noqa: BLE001 - Supervisor raises several types
        _LOGGER.debug("Could not read the ADB server app state: %s", error)
        return "missing"
    return "started" if str(info.state) == "started" else "stopped"


async def async_install_server_app(hass: Any) -> None:
    """Add this repository to the app store, install the app, and start it."""

    if not supervisor_available(hass):
        raise AdbSetupError("server_unsupported")
    try:
        from aiohasupervisor.models import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
            StoreAddRepository,
        )

        client = _supervisor(hass)
        app = await _async_find_store_app(client)
        if app is None:
            _LOGGER.info("Adding %s to the app store", APP_REPOSITORY)
            await client.store.add_repository(
                StoreAddRepository(repository=APP_REPOSITORY)
            )
            app = await _async_find_store_app(client)
        if app is None:
            raise AdbSetupError("server_install_failed", "app not found in the store")
        if not app.installed:
            _LOGGER.info("Installing the ADB server app %s", app.slug)
            await client.store.install_addon(app.slug)
    except AdbSetupError:
        raise
    except Exception as error:  # noqa: BLE001
        raise AdbSetupError("server_install_failed", str(error)) from error
    await async_server_address(hass)


async def async_server_address(hass: Any) -> tuple[str, int]:
    """Start the app if needed and return where its adb server listens."""

    try:
        client = _supervisor(hass)
        app = await _async_find_store_app(client)
        if app is None or not app.installed:
            raise AdbSetupError("server_missing")
        info = await client.addons.addon_info(app.slug)
        if str(info.state) != "started":
            await client.addons.start_addon(app.slug)
        hostname = str(info.hostname)
    except AdbSetupError:
        raise
    except Exception as error:  # noqa: BLE001
        raise AdbSetupError("server_start_failed", str(error)) from error

    address = (hostname, ADB_SERVER_PORT)
    deadline = time.monotonic() + APP_START_TIMEOUT_S
    while True:
        try:
            await async_query(address, "host:version")
        except AdbSetupError:
            if time.monotonic() > deadline:
                raise AdbSetupError(
                    "server_start_failed", "the adb server did not answer"
                ) from None
            await asyncio.sleep(2)
        else:
            return address


# --- Pairing and connecting ------------------------------------------------


async def async_pair_wireless(
    hass: Any,
    host: str,
    *,
    code: str,
    pairing_port: int | None = None,
    connect_port: int | None = None,
) -> str:
    """Pair the ADB server with the TV's Wireless debugging, then connect.

    Ports left out are looked up over mDNS (the TV advertises both while the
    pairing code is on screen). Returns the connected 'ip:port' serial.
    """

    address = await async_server_address(hass)
    ip = await _async_resolve(host)
    if pairing_port is None:
        pairing_port = await async_find_service_port(hass, ip, PAIRING_SERVICE)
    if pairing_port is None:
        raise AdbSetupError("wireless_pairing_port")

    result = await async_query(address, f"host:pair:{code.strip()}:{ip}:{pairing_port}")
    if "successfully paired" not in result.lower():
        raise AdbSetupError("wireless_pair_failed", result.strip()[-200:])
    _LOGGER.info("Paired the ADB server with %s", ip)
    return await _async_connect_server(hass, address, ip, connect_port)


async def async_wireless_device(hass: Any, host: str) -> AdbServerDevice | None:
    """A device over Wireless debugging, or None when that isn't set up here.

    None means: no Supervisor, or the ADB server app isn't installed, so the
    caller should report the original port 5555 error.
    """

    if await async_server_app_state(hass) in ("unavailable", "missing"):
        return None
    address = await async_server_address(hass)
    ip = await _async_resolve(host)
    serial = await _async_connect_server(hass, address, ip, None)
    return AdbServerDevice(address, serial)


async def _async_connect_server(
    hass: Any, address: tuple[str, int], ip: str, port: int | None
) -> str:
    if port is None:
        port = await async_find_service_port(hass, ip, CONNECT_SERVICE)
    if port is None:
        raise AdbSetupError("wireless_off")
    serial = f"{ip}:{port}"
    result = (await async_query(address, f"host:connect:{serial}")).lower()
    if "connected to" not in result:
        raise AdbSetupError("wireless_not_paired", result.strip()[-200:])
    # A key the TV hasn't paired with connects and then drops; check it's usable.
    # The connection can take a moment to come up after 'connected to'.
    device = AdbServerDevice(address, serial)
    for attempt in range(5):
        try:
            await device.shell("echo ok", read_timeout_s=10.0)
        except AdbSetupError as error:
            if attempt == 4:
                raise AdbSetupError("wireless_not_paired", error.detail) from error
            await asyncio.sleep(1.5)
        else:
            return serial
    return serial


async def _async_resolve(host: str) -> str:
    """The TV's IPv4 address; mDNS records and adb serials use addresses."""

    try:
        socket.inet_aton(host)
    except OSError:
        pass
    else:
        return host
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, family=socket.AF_INET)
    except OSError as error:
        raise AdbSetupError("not_reachable", str(error)) from error
    return str(infos[0][4][0])


async def async_find_service_port(
    hass: Any, ip: str, service_type: str, timeout_s: float = DISCOVERY_TIMEOUT_S
) -> int | None:
    """Find the port the TV at `ip` advertises for an adb mDNS service."""

    try:
        from homeassistant.components import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
            zeroconf as ha_zeroconf,
        )
        from zeroconf import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
            ServiceStateChange,
        )
        from zeroconf.asyncio import (  # type: ignore[import-not-found,import-untyped,unused-ignore]
            AsyncServiceBrowser,
            AsyncServiceInfo,
        )
    except ModuleNotFoundError:
        return None

    aiozc = await ha_zeroconf.async_get_async_instance(hass)
    names: set[str] = set()

    def on_change(
        zeroconf: Any, service_type: str, name: str, state_change: Any
    ) -> None:
        if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
            names.add(name)

    browser = AsyncServiceBrowser(aiozc.zeroconf, [service_type], handlers=[on_change])
    try:
        await asyncio.sleep(timeout_s)
    finally:
        await browser.async_cancel()

    for name in names:
        info = AsyncServiceInfo(service_type, name)
        if await info.async_request(aiozc.zeroconf, 3000) and ip in (
            info.parsed_addresses()
        ):
            return int(info.port) if info.port else None
    return None


# --- adb server protocol ---------------------------------------------------


def encode_request(request: str) -> bytes:
    """adb server requests are a 4-digit hex length followed by the text."""

    data = request.encode()
    return f"{len(data):04x}".encode() + data


async def _async_open(
    address: tuple[str, int],
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        return await asyncio.wait_for(
            asyncio.open_connection(*address), SERVER_CONNECT_TIMEOUT_S
        )
    except (OSError, TimeoutError) as error:
        raise AdbSetupError("server_unreachable", str(error)) from error


async def _async_status(reader: asyncio.StreamReader) -> None:
    status = await reader.readexactly(4)
    if status == b"OKAY":
        return
    if status == b"FAIL":
        raise AdbSetupError("adb_failed", await _async_read_message(reader))
    raise AdbSetupError("adb_failed", f"unexpected reply {status!r}")


async def _async_read_message(reader: asyncio.StreamReader) -> str:
    length = int((await reader.readexactly(4)).decode(), 16)
    return (await reader.readexactly(length)).decode(errors="replace")


async def _async_close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001
        pass


async def async_query(
    address: tuple[str, int], request: str, timeout_s: float = 30.0
) -> str:
    """Send a 'host:' request and return the server's reply text."""

    reader, writer = await _async_open(address)
    try:
        writer.write(encode_request(request))
        await writer.drain()
        await asyncio.wait_for(_async_status(reader), timeout_s)
        return await asyncio.wait_for(_async_read_message(reader), timeout_s)
    except AdbSetupError as error:
        # A failed connect or pair still answers; return its text to the caller.
        if error.code == "adb_failed" and request.startswith(
            ("host:connect:", "host:pair:")
        ):
            return error.detail or ""
        raise
    except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError) as error:
        raise AdbSetupError("server_unreachable", str(error)) from error
    finally:
        await _async_close(writer)


class AdbServerDevice:
    """The subset of adb-shell's device API that adb_setup uses."""

    def __init__(self, address: tuple[str, int], serial: str) -> None:
        self.address = address
        self.serial = serial

    async def _async_transport(
        self, service: str
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await _async_open(self.address)
        try:
            writer.write(encode_request(f"host:transport:{self.serial}"))
            await writer.drain()
            await _async_status(reader)
            writer.write(encode_request(service))
            await writer.drain()
            await _async_status(reader)
        except BaseException:
            await _async_close(writer)
            raise
        return reader, writer

    async def shell(
        self,
        command: str,
        read_timeout_s: float = 30.0,
        timeout_s: float | None = None,
    ) -> str:
        """Run a shell command and return its combined output."""

        try:
            reader, writer = await self._async_transport(f"shell:{command}")
            try:
                data = await asyncio.wait_for(
                    reader.read(), timeout_s or max(read_timeout_s, 30.0)
                )
            finally:
                await _async_close(writer)
        except AdbSetupError:
            raise
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as error:
            raise AdbSetupError("adb_failed", str(error) or "timeout") from error
        return data.decode(errors="replace")

    async def push(
        self, local_path: str, device_path: str, read_timeout_s: float = 180.0
    ) -> None:
        """Copy a file to the TV with the adb sync protocol."""

        data = await asyncio.to_thread(_read_file, local_path)
        try:
            reader, writer = await self._async_transport("sync:")
            try:
                await asyncio.wait_for(
                    _async_sync_send(reader, writer, data, device_path),
                    read_timeout_s,
                )
            finally:
                await _async_close(writer)
        except AdbSetupError:
            raise
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as error:
            raise AdbSetupError("adb_failed", str(error) or "timeout") from error

    async def close(self) -> None:
        """Nothing to close: each command uses its own server connection."""


def sync_packets(data: bytes, device_path: str, mtime: int) -> list[bytes]:
    """The SEND/DATA/DONE packets that write `data` to `device_path`."""

    header = f"{device_path},{PUSH_FILE_MODE}".encode()
    packets = [b"SEND" + struct.pack("<I", len(header)) + header]
    for start in range(0, len(data), SYNC_CHUNK):
        chunk = data[start : start + SYNC_CHUNK]
        packets.append(b"DATA" + struct.pack("<I", len(chunk)) + chunk)
    packets.append(b"DONE" + struct.pack("<I", mtime))
    return packets


async def _async_sync_send(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    data: bytes,
    device_path: str,
) -> None:
    for packet in sync_packets(data, device_path, int(time.time())):
        writer.write(packet)
        await writer.drain()
    reply = await reader.readexactly(8)
    status, length = reply[:4], struct.unpack("<I", reply[4:])[0]
    if status != b"OKAY":
        message = (await reader.readexactly(length)).decode(errors="replace")
        raise AdbSetupError("adb_failed", f"push failed: {message}")
    writer.write(b"QUIT" + struct.pack("<I", 0))
    await writer.drain()


def _read_file(path: str) -> bytes:
    with open(os.fspath(path), "rb") as handle:
        return handle.read()
