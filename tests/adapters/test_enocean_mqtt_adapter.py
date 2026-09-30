from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from obs.adapters.enocean_mqtt.adapter import (
    GATEWAY_DEVICE_ID,
    EnoceanMqttAdapter,
    EnoceanMqttAdapterConfig,
    EnoceanMqttBindingConfig,
    extract_value,
)
from obs.api.v1 import adapters as adapters_api
from obs.api.v1.redaction import REDACTED
from tests.adapters.conftest import make_binding


def test_config_defaults_to_localhost_api():
    cfg = EnoceanMqttAdapterConfig()

    assert cfg.host == "localhost"
    assert cfg.port == 8001
    assert cfg.base_url == "http://localhost:8001"


def test_config_normalizes_host():
    cfg = EnoceanMqttAdapterConfig(host=" gateway ", port=8001)

    assert cfg.host == "gateway"
    assert cfg.base_url == "http://gateway:8001"


def test_api_config_redacts_and_preserves_token():
    stored = {"host": "gateway", "port": 8001, "token": "secret-token"}

    visible = adapters_api._redact_instance_config("ENOCEAN", stored)
    merged = adapters_api._preserve_redacted_enocean_token(stored, visible)

    assert visible == {"host": "gateway", "port": 8001, "token": REDACTED}
    assert merged == stored


def test_redacted_token_without_stored_secret_is_rejected():
    with pytest.raises(ValueError, match="re-enter credentials"):
        adapters_api._preserve_redacted_enocean_token({}, {"token": REDACTED})


def test_config_migrates_legacy_base_url():
    cfg = EnoceanMqttAdapterConfig(base_url=" http://gateway:8001/ ")

    assert cfg.host == "gateway"
    assert cfg.port == 8001
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
    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001, "token": "secret"})
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

    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001})
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gateway:8001")
    binding = make_binding({"datapoint_id": "th_sensor.temperature"})

    assert await adapter.read(binding) == 22.1

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_write_posts_datapoint_value(mock_bus):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/datapoints/thermostat.setpoint/value"
        assert request.content == b'{"value":21.5}'
        return httpx.Response(
            200,
            json={
                "status": "accepted",
                "datapoint_id": "thermostat.setpoint",
                "value": 21.5,
            },
        )

    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001})
    adapter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://gateway:8001",
    )
    binding = make_binding({"datapoint_id": "thermostat.setpoint"})

    await adapter.write(binding, 21.5)
    await adapter.disconnect()


@pytest.mark.asyncio
async def test_write_requires_connection(mock_bus):
    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001})
    binding = make_binding({"datapoint_id": "thermostat.setpoint"})

    with pytest.raises(RuntimeError, match="not connected"):
        await adapter.write(binding, 21.5)


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

    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001})
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gateway:8001")

    devices = await adapter.browse_devices()

    assert devices[0] == (
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
    )
    assert devices[1]["id"] == GATEWAY_DEVICE_ID
    assert devices[1]["device_name"] == "enocean-mqtt Gateway"
    assert devices[1]["datapoints_count"] == 5

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

    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001})
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gateway:8001")

    readable = await adapter.browse_devices("SOURCE")
    writable = await adapter.browse_devices("DEST")
    all_devices = await adapter.browse_devices("BOTH")

    assert [item["id"] for item in readable] == ["physical_sensor", GATEWAY_DEVICE_ID]
    assert [item["id"] for item in writable] == ["virtual_sensor"]
    assert [item["id"] for item in all_devices] == [
        "physical_sensor",
        "virtual_sensor",
        GATEWAY_DEVICE_ID,
    ]

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_browse_gateway_status_datapoints(mock_bus):
    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001})
    adapter._client = AsyncMock()

    datapoints = await adapter.browse_datapoints(GATEWAY_DEVICE_ID, "SOURCE")

    assert [item["id"] for item in datapoints] == [
        "gateway.online",
        "gateway.enocean_connected",
        "gateway.tx_enabled",
        "gateway.mqtt_connected",
        "gateway.devices_total",
    ]
    assert datapoints[-1]["data_type"] == "INTEGER"
    adapter._client.get.assert_not_called()

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_read_fetches_gateway_status_value(mock_bus):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/gateway/adapter-status"
        return httpx.Response(
            200,
            json={"values": {"gateway.devices_total": 17}},
        )

    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001})
    adapter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://gateway:8001",
    )
    binding = make_binding({"datapoint_id": "gateway.devices_total"})

    assert await adapter.read(binding) == 17
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
                    {"id": "th.telegram_type", "name": "telegram_type", "data_type": "enum", "readable": True},
                    {"id": "th.setpoint", "name": "Setpoint", "data_type": "float", "writable": True},
                ]
            },
        )

    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001})
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gateway:8001")

    readable = await adapter.browse_datapoints("th_sensor", "SOURCE")
    writable = await adapter.browse_datapoints("th_sensor", "DEST")
    all_datapoints = await adapter.browse_datapoints("th_sensor", "BOTH")

    assert [item["id"] for item in readable] == ["th.temperature", "th.telegram_type"]
    assert readable[0]["data_type"] == "FLOAT"
    assert readable[1]["data_type"] == "STRING"
    assert [item["id"] for item in writable] == ["th.setpoint"]
    assert [item["id"] for item in all_datapoints] == ["th.temperature", "th.telegram_type", "th.setpoint"]

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_on_bindings_reloaded_starts_source_stream_task(mock_bus):
    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001})
    adapter._client = AsyncMock()
    adapter._connected = True
    binding = make_binding({"datapoint_id": "th_sensor.temperature"})

    async def stream_loop():
        await asyncio.Future()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(adapter, "_stream_loop", stream_loop)
        await adapter.reload_bindings([binding])

    assert adapter._stream_task is not None
    assert adapter._datapoint_map["th_sensor.temperature"] == [binding]

    await adapter.disconnect()


@pytest.mark.asyncio
async def test_dispatch_sse_datapoint_event_publishes_bound_value(mock_bus):
    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001})
    binding = make_binding({"datapoint_id": "th_sensor.temperature"})
    adapter._datapoint_map = {"th_sensor.temperature": [binding]}

    await adapter._dispatch_sse_event(
        "datapoint",
        [
            (
                '{"datapoint_id":"th_sensor.temperature",'
                '"value":22.4,"quality":"good","unit":"°C"}'
            )
        ],
    )

    event = mock_bus.publish.call_args.args[0]

    assert event.datapoint_id == binding.datapoint_id
    assert event.value == 22.4
    assert event.quality == "good"
    assert event.source_adapter == "ENOCEAN"
    assert event.binding_id == binding.id


@pytest.mark.asyncio
async def test_dispatch_sse_ignores_unbound_datapoint(mock_bus):
    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001})
    adapter._datapoint_map = {}

    await adapter._dispatch_sse_event(
        "datapoint",
        ['{"datapoint_id":"other.temperature","value":22.4,"quality":"good"}'],
    )

    mock_bus.publish.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_gateway_status_event_publishes_bound_values(mock_bus):
    adapter = EnoceanMqttAdapter(mock_bus, {"host": "gateway", "port": 8001})
    online = make_binding({"datapoint_id": "gateway.online"})
    devices = make_binding({"datapoint_id": "gateway.devices_total"})
    adapter._datapoint_map = {
        "gateway.online": [online],
        "gateway.devices_total": [devices],
    }

    await adapter._dispatch_sse_event(
        "gateway_status",
        [
            (
                '{"values":{"gateway.online":true,'
                '"gateway.devices_total":17}}'
            )
        ],
    )

    events = [call.args[0] for call in mock_bus.publish.call_args_list]
    assert [(event.binding_id, event.value, event.quality) for event in events] == [
        (online.id, True, "good"),
        (devices.id, 17, "good"),
    ]
