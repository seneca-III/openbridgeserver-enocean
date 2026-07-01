from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from obs.adapters.enocean_mqtt.adapter import (
    EnoceanMqttAdapter,
    EnoceanMqttAdapterConfig,
    EnoceanMqttBindingConfig,
    extract_value,
)
from tests.adapters.conftest import make_binding


def test_config_normalizes_base_url():
    cfg = EnoceanMqttAdapterConfig(base_url=" http://gateway:8001/ ")

    assert cfg.base_url == "http://gateway:8001"


def test_binding_rejects_empty_datapoint_id():
    with pytest.raises(ValueError):
        EnoceanMqttBindingConfig(datapoint_id=" ")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"value": 21.5}, 21.5),
        ({"value": {"value": True, "unit": None}}, True),
        ({"datapoint": {"value": "open"}}, "open"),
        ({"unexpected": 1}, {"unexpected": 1}),
        (42, 42),
    ],
)
def test_extract_value(payload, expected):
    assert extract_value(payload) == expected


@pytest.mark.asyncio
async def test_connect_sends_bearer_token_and_marks_connected(mock_bus):
    seen_headers = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(handler)
    adapter = EnoceanMqttAdapter(mock_bus, {"base_url": "http://gateway:8001", "token": "secret"})
    adapter._client = httpx.AsyncClient(transport=transport, base_url="http://gateway:8001")

    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        headers = kwargs.get("headers") or {}
        return original_client(transport=transport, base_url=kwargs["base_url"], headers=headers, timeout=kwargs["timeout"])

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(httpx, "AsyncClient", client_factory)
        await adapter.connect()

    assert adapter.connected is True
    assert seen_headers["authorization"] == "Bearer secret"

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_read_fetches_datapoint_value(mock_bus):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/datapoints/th_sensor.temperature/value"
        return httpx.Response(200, json={"value": {"value": 22.1}})

    adapter = EnoceanMqttAdapter(mock_bus, {"base_url": "http://gateway:8001"})
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gateway:8001")
    binding = make_binding({"datapoint_id": "th_sensor.temperature"})

    assert await adapter.read(binding) == 22.1

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_browse_devices_normalizes_api_payload(mock_bus):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/devices"
        return httpx.Response(
            200,
            json={
                "devices": [
                    {
                        "id": "th_sensor",
                        "device_name": "TH Sensor",
                        "alias": "th_sensor",
                        "eep": "A5-04-01",
                        "readable": True,
                        "writable": False,
                        "datapoints": [{"id": "temperature"}],
                    }
                ]
            },
        )

    adapter = EnoceanMqttAdapter(mock_bus, {"base_url": "http://gateway:8001"})
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gateway:8001")

    assert await adapter.browse_devices() == [
        {
            "id": "th_sensor",
            "device_name": "TH Sensor",
            "name": "TH Sensor",
            "alias": "th_sensor",
            "eep": "A5-04-01",
            "manufacturer": None,
            "source_type": None,
            "virtual_device_id": None,
            "readable": True,
            "writable": False,
            "datapoints_count": 1,
        }
    ]

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_browse_devices_filters_by_direction(mock_bus):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/devices"
        return httpx.Response(
            200,
            json={
                "devices": [
                    {"id": "physical_sensor", "device_name": "Physical Sensor", "readable": True, "writable": False},
                    {
                        "id": "virtual_sensor",
                        "device_name": "Virtual Sensor",
                        "source_type": "virtual_device",
                        "readable": False,
                        "writable": True,
                    },
                ]
            },
        )

    adapter = EnoceanMqttAdapter(mock_bus, {"base_url": "http://gateway:8001"})
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gateway:8001")

    readable = await adapter.browse_devices("SOURCE")
    writable = await adapter.browse_devices("DEST")
    all_devices = await adapter.browse_devices("BOTH")

    assert [item["id"] for item in readable] == ["physical_sensor"]
    assert [item["id"] for item in writable] == ["virtual_sensor"]
    assert [item["id"] for item in all_devices] == ["physical_sensor", "virtual_sensor"]

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_browse_datapoints_filters_by_direction_and_maps_type(mock_bus):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/devices/th_sensor/datapoints"
        return httpx.Response(
            200,
            json={
                "datapoints": [
                    {"id": "th.temperature", "name": "Temperature", "data_type": "number", "readable": True},
                    {"id": "th.setpoint", "name": "Setpoint", "data_type": "float", "writable": True},
                ]
            },
        )

    adapter = EnoceanMqttAdapter(mock_bus, {"base_url": "http://gateway:8001"})
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gateway:8001")

    readable = await adapter.browse_datapoints("th_sensor", "SOURCE")
    writable = await adapter.browse_datapoints("th_sensor", "DEST")
    all_datapoints = await adapter.browse_datapoints("th_sensor", "BOTH")

    assert [item["id"] for item in readable] == ["th.temperature"]
    assert readable[0]["data_type"] == "FLOAT"
    assert [item["id"] for item in writable] == ["th.setpoint"]
    assert [item["id"] for item in all_datapoints] == ["th.temperature", "th.setpoint"]

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_on_bindings_reloaded_starts_source_poll_task(mock_bus):
    adapter = EnoceanMqttAdapter(mock_bus, {"base_url": "http://gateway:8001", "poll_interval": 1})
    adapter._client = AsyncMock()
    adapter._connected = True
    binding = make_binding({"datapoint_id": "th_sensor.temperature"})

    await adapter.reload_bindings([binding])

    assert len(adapter._poll_tasks) == 1

    await adapter.disconnect()
