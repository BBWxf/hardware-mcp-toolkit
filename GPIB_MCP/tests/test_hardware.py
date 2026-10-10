from __future__ import annotations

import os

import pytest

from gpib_mcp.gpib import GpibService, IOSettings

pytestmark = pytest.mark.skipif(
    os.getenv("GPIB_MCP_HARDWARE_TEST") != "1",
    reason="真实 GPIB 硬件测试未启用",
)


def test_real_gpib_resource_can_identify() -> None:
    resource = os.getenv("GPIB_MCP_TEST_RESOURCE")
    if not resource:
        pytest.fail("启用硬件测试时必须设置 GPIB_MCP_TEST_RESOURCE")

    service = GpibService()
    try:
        resources = service.scan()
        assert isinstance(resources, list)
        response = service.identify(resource, IOSettings(timeout_ms=5_000))
        assert response.strip()
    finally:
        service.close()
