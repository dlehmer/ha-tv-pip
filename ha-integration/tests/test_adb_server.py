"""Tests for the Wireless debugging path through the ADB server app."""

import asyncio
import struct
from pathlib import Path
from typing import Any

import pytest

from custom_components.ha_tv_pip_plus.adb_server import (
    PUSH_FILE_MODE,
    SYNC_CHUNK,
    AdbServerDevice,
    async_query,
    encode_request,
    is_server_app_slug,
    supervisor_available,
    sync_packets,
)
from custom_components.ha_tv_pip_plus.adb_setup import ERROR_HINTS, AdbSetupError


def test_encode_request_uses_hex_length() -> None:
    assert encode_request("host:version") == b"000chost:version"


def test_server_app_slug_matches_store_slugs() -> None:
    assert is_server_app_slug("ha_tv_pip_adb")
    assert is_server_app_slug("1a2b3c4d_ha_tv_pip_adb")
    assert not is_server_app_slug("a0d7b954_adb")


def test_supervisor_available_reads_loaded_components() -> None:
    class Config:
        components = {"hassio", "http"}

    class Hass:
        config = Config()

    assert supervisor_available(Hass())
    Hass.config.components = {"http"}
    assert not supervisor_available(Hass())


def test_sync_packets_chunk_the_file() -> None:
    data = b"x" * (SYNC_CHUNK + 10)
    packets = sync_packets(data, "/data/local/tmp/a.apk", 1234)
    header = f"/data/local/tmp/a.apk,{PUSH_FILE_MODE}".encode()
    assert packets[0] == b"SEND" + struct.pack("<I", len(header)) + header
    assert packets[1][:8] == b"DATA" + struct.pack("<I", SYNC_CHUNK)
    assert packets[2] == b"DATA" + struct.pack("<I", 10) + b"x" * 10
    assert packets[3] == b"DONE" + struct.pack("<I", 1234)


def test_new_error_codes_have_hints() -> None:
    for code in (
        "server_unsupported",
        "server_install_failed",
        "wireless_pairing_port",
        "wireless_pair_failed",
        "wireless_off",
        "wireless_not_paired",
    ):
        assert code in ERROR_HINTS


class FakeAdbServer:
    """Speaks just enough of the adb server protocol for the client."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.pushed: dict[str, bytes] = {}

    async def _request(self, reader: asyncio.StreamReader) -> str:
        length = int((await reader.readexactly(4)).decode(), 16)
        request = (await reader.readexactly(length)).decode()
        self.requests.append(request)
        return request

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request = await self._request(reader)
        if request.startswith("host:transport:"):
            writer.write(b"OKAY")
            service = await self._request(reader)
            writer.write(b"OKAY")
            if service.startswith("shell:"):
                writer.write(f"ran {service[6:]}\n".encode())
            elif service == "sync:":
                await self._sync(reader, writer)
        elif request.startswith("host:connect:"):
            reply = f"connected to {request.removeprefix('host:connect:')}"
            writer.write(b"OKAY" + encode_request(reply))
        elif request.startswith("host:pair:"):
            writer.write(b"FAIL" + encode_request("Failed: Wrong password"))
        else:
            writer.write(b"OKAY" + encode_request("0029"))
        await writer.drain()
        writer.close()

    async def _sync(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        path = ""
        data = b""
        while True:
            command = await reader.readexactly(4)
            length = struct.unpack("<I", await reader.readexactly(4))[0]
            if command == b"SEND":
                path = (await reader.readexactly(length)).decode().split(",")[0]
            elif command == b"DATA":
                data += await reader.readexactly(length)
            elif command == b"DONE":
                self.pushed[path] = data
                writer.write(b"OKAY" + struct.pack("<I", 0))
            elif command == b"QUIT":
                return


async def _with_server(test: Any) -> Any:
    fake = FakeAdbServer()
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    address = server.sockets[0].getsockname()[:2]
    try:
        return await test(fake, (address[0], address[1]))
    finally:
        server.close()
        await server.wait_closed()


def test_query_connect_and_failed_pair() -> None:
    async def run(fake: FakeAdbServer, address: tuple[str, int]) -> None:
        assert await async_query(address, "host:version") == "0029"
        assert await async_query(address, "host:connect:10.0.0.5:4321") == (
            "connected to 10.0.0.5:4321"
        )
        # A refused pairing still reports its message instead of raising.
        assert "Wrong password" in await async_query(
            address, "host:pair:123456:10.0.0.5:37000"
        )

    asyncio.run(_with_server(run))


def test_shell_and_push_over_transport(tmp_path: Path) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk-bytes" * 20_000)

    async def run(fake: FakeAdbServer, address: tuple[str, int]) -> None:
        device = AdbServerDevice(address, "10.0.0.5:4321")
        assert await device.shell("pm list packages") == "ran pm list packages\n"
        await device.push(str(apk), "/data/local/tmp/app.apk")
        assert fake.pushed["/data/local/tmp/app.apk"] == apk.read_bytes()
        assert "host:transport:10.0.0.5:4321" in fake.requests

    asyncio.run(_with_server(run))


def test_unreachable_server_raises_setup_error() -> None:
    async def run() -> None:
        with pytest.raises(AdbSetupError) as error:
            await async_query(("127.0.0.1", 1), "host:version")
        assert error.value.code == "server_unreachable"

    asyncio.run(run())
