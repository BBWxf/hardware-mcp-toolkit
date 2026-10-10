from __future__ import annotations

import asyncio
import base64

from mcp import Client

from gpib_mcp.server import create_server


async def _call_tool(server, name: str, arguments: dict):
    async with Client(server) as client:
        return await client.call_tool(name, arguments)


def test_tools_are_registered(gpib_service) -> None:
    async def run() -> set[str]:
        async with Client(create_server(gpib_service)) as client:
            tools = await client.list_tools()
            return {tool.name for tool in tools.tools}

    assert asyncio.run(run()) == {
        "gpib_scan",
        "gpib_identify",
        "gpib_query",
        "gpib_write",
        "gpib_query_raw",
        "gpib_read_raw",
        "chroma_discover",
        "chroma_6530_configure_output",
        "chroma_66205_read_input",
        "chroma_63200a_set_cc_current",
        "chroma_63200a_set_cv_voltage",
        "chroma_63200a_set_load_enabled",
        "chroma_63200a_read_measurements",
    }


def test_scan_and_query_through_mcp(gpib_service) -> None:
    server = create_server(gpib_service)

    scan = asyncio.run(_call_tool(server, "gpib_scan", {}))
    assert scan.structured_content == {
        "count": 2,
        "resources": ["GPIB0::12::INSTR", "GPIB0::5::INSTR"],
    }

    query = asyncio.run(
        _call_tool(
            server,
            "gpib_query",
            {
                "resource": "GPIB0::5::INSTR",
                "command": "MEAS:VOLT?",
            },
        )
    )
    assert query.structured_content == {
        "resource": "GPIB0::5::INSTR",
        "response": "1.2345",
    }


def test_write_is_not_filtered(gpib_service) -> None:
    result = asyncio.run(
        _call_tool(
            create_server(gpib_service),
            "gpib_write",
            {
                "resource": "GPIB0::5::INSTR",
                "command": "OUTP ON",
            },
        )
    )

    assert result.is_error is False
    assert result.structured_content["bytes_written"] > 0


def test_raw_query_returns_base64(gpib_service) -> None:
    result = asyncio.run(
        _call_tool(
            create_server(gpib_service),
            "gpib_query_raw",
            {
                "resource": "GPIB0::5::INSTR",
                "command": "WAV:DATA?",
            },
        )
    )

    content = result.structured_content
    assert content["encoding"] == "base64"
    assert base64.b64decode(content["data_base64"]) == b"\x00\x01DEMO-WAVEFORM\xff"
    assert content["byte_count"] == 16


def test_tool_error_does_not_break_server(gpib_service) -> None:
    async def run() -> tuple[bool, dict]:
        async with Client(create_server(gpib_service)) as client:
            failed = await client.call_tool(
                "gpib_query",
                {"resource": "TCPIP0::host::INSTR", "command": "*IDN?"},
            )
            succeeded = await client.call_tool("gpib_scan", {})
            return failed.is_error, succeeded.structured_content

    failed, succeeded = asyncio.run(run())
    assert failed is True
    assert succeeded["count"] == 2
