"""enocean-mqtt REST API adapter.

Consumes the enocean-mqtt API v1 as a semantic source and destination.
EnOcean, EEP and datapoint semantics stay in enocean-mqtt; this adapter maps
API datapoint values and writes to open bridge bindings.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, Field, field_validator, model_validator

from obs.adapters.base import AdapterBase
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent

logger = logging.getLogger(__name__)
SSE_RECONNECT_DELAY_SECONDS = 10.0
GATEWAY_DEVICE_ID = "__gateway__"
GATEWAY_STATUS_DATAPOINTS: tuple[dict[str, Any], ...] = (
    {
        "id": "gateway.online",
        "device_id": GATEWAY_DEVICE_ID,
        "name": "Online",
        "data_type": "BOOLEAN",
        "readable": True,
        "writable": False,
        "role": "system.gateway.online",
    },
    {
        "id": "gateway.enocean_connected",
        "device_id": GATEWAY_DEVICE_ID,
        "name": "EnOcean verbunden",
        "data_type": "BOOLEAN",
        "readable": True,
        "writable": False,
        "role": "system.enocean.connected",
    },
    {
        "id": "gateway.tx_enabled",
        "device_id": GATEWAY_DEVICE_ID,
        "name": "EnOcean TX aktiviert",
        "data_type": "BOOLEAN",
        "readable": True,
        "writable": False,
        "role": "system.enocean.tx_enabled",
    },
    {
        "id": "gateway.mqtt_connected",
        "device_id": GATEWAY_DEVICE_ID,
        "name": "MQTT verbunden",
        "data_type": "BOOLEAN",
        "readable": True,
        "writable": False,
        "role": "system.mqtt.connected",
    },
    {
        "id": "gateway.devices_total",
        "device_id": GATEWAY_DEVICE_ID,
        "name": "Geräte gesamt",
        "data_type": "INTEGER",
        "readable": True,
        "writable": False,
        "role": "system.devices.count",
    },
)
GATEWAY_STATUS_IDS = frozenset(item["id"] for item in GATEWAY_STATUS_DATAPOINTS)


class EnoceanMqttAdapterConfig(BaseModel):
    host: str = Field(default="localhost")
    port: int = Field(default=8001, ge=1, le=65535)
    token: str | None = Field(default=None, json_schema_extra={"format": "password"})
    timeout: float = Field(default=10.0, ge=1.0)

    @model_validator(mode="before")
    @classmethod
    def _migrate_base_url(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        base_url = data.get("base_url")
        if not base_url:
            return data

        parsed = urlparse(str(base_url).strip())
        migrated = dict(data)
        if "host" not in migrated and parsed.hostname:
            migrated["host"] = parsed.hostname
        if "port" not in migrated and parsed.port:
            migrated["port"] = parsed.port
        migrated.pop("base_url", None)
        return migrated

    @field_validator("host")
    @classmethod
    def _normalize_host(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("host must not be empty")
        return normalized

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class EnoceanMqttBindingConfig(BaseModel):
    datapoint_id: str = Field(description="enocean-mqtt API datapoint id")
    device_id: str | None = Field(default=None, description="enocean-mqtt API device id")

    @field_validator("datapoint_id")
    @classmethod
    def _normalize_datapoint_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("datapoint_id must not be empty")
        return normalized


@register
class EnoceanMqttAdapter(AdapterBase):
    adapter_type = "ENOCEAN"
    config_schema = EnoceanMqttAdapterConfig
    binding_config_schema = EnoceanMqttBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._cfg = EnoceanMqttAdapterConfig(**(config or {}))
        self._client: httpx.AsyncClient | None = None
        self._stream_task: asyncio.Task | None = None
        self._datapoint_map: dict[str, list[Any]] = {}

    async def connect(self) -> None:
        self._cfg = EnoceanMqttAdapterConfig(**self._config)
        headers = {}
        if self._cfg.token:
            headers["Authorization"] = f"Bearer {self._cfg.token}"

        self._client = httpx.AsyncClient(
            base_url=self._cfg.base_url,
            headers=headers,
            timeout=httpx.Timeout(self._cfg.timeout, read=None),
        )

        try:
            response = await self._client.get("/api/v1/gateway/status")
            response.raise_for_status()
        except Exception as exc:
            logger.warning("enocean-mqtt connection test failed: %s", exc)
            await self._publish_status(False, f"Connection failed: {exc}")
            return

        await self._publish_status(
            True,
            f"Connected to {self._cfg.base_url}",
        )
        logger.info("enocean-mqtt adapter connected: %s", self._cfg.base_url)

    async def disconnect(self) -> None:
        if self._stream_task is not None:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None
        self._datapoint_map.clear()

        if self._client is not None:
            await self._client.aclose()
            self._client = None

        await self._publish_status(False, "Disconnected", code="disconnected")

    async def _on_bindings_reloaded(self) -> None:
        if self._stream_task is not None:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None
        self._datapoint_map.clear()

        if self._client is None or not self.connected:
            return

        for binding in self._bindings:
            if binding.direction not in ("SOURCE", "BOTH"):
                continue
            try:
                cfg = EnoceanMqttBindingConfig(**binding.config)
            except Exception:
                logger.warning("Invalid enocean-mqtt binding config for %s — skipped", binding.id)
                continue
            self._datapoint_map.setdefault(cfg.datapoint_id, []).append(binding)

        if self._datapoint_map:
            self._stream_task = asyncio.create_task(
                self._stream_loop(),
                name="enocean-mqtt-sse",
            )

        logger.info(
            "enocean-mqtt adapter: %d datapoint subscription(s)",
            len(self._datapoint_map),
        )

    async def _stream_loop(self) -> None:
        paths = []
        if any(datapoint_id not in GATEWAY_STATUS_IDS for datapoint_id in self._datapoint_map):
            paths.append("/api/v1/datapoints/stream")
        if any(datapoint_id in GATEWAY_STATUS_IDS for datapoint_id in self._datapoint_map):
            paths.append("/api/v1/gateway/adapter-status/stream")

        await asyncio.gather(*(self._stream_endpoint_loop(path) for path in paths))

    async def _stream_endpoint_loop(self, path: str) -> None:
        while True:
            try:
                await self._consume_stream(path)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "enocean-mqtt SSE stream failed, retrying in %.1f s: %s",
                    SSE_RECONNECT_DELAY_SECONDS,
                    exc,
                )
                await self._publish_status(
                    True,
                    "SSE stream interrupted; reconnecting",
                    severity="warning",
                    code="sseReconnecting",
                )
                if path == "/api/v1/gateway/adapter-status/stream":
                    await self._publish_gateway_online(False, quality="bad")
                await self._read_bound_values_once()
            await asyncio.sleep(SSE_RECONNECT_DELAY_SECONDS)

    async def _consume_stream(self, path: str) -> None:
        if self._client is None:
            return

        async with self._client.stream(
            "GET",
            path,
            params={"include_initial": "true"},
        ) as response:
            response.raise_for_status()
            await self._publish_status(
                True,
                f"Connected to {self._cfg.base_url}",
            )
            event_name: str | None = None
            data_lines: list[str] = []

            async for line in response.aiter_lines():
                if line.startswith(":"):
                    continue
                if line == "":
                    await self._dispatch_sse_event(event_name, data_lines)
                    event_name = None
                    data_lines = []
                    continue
                field, separator, value = line.partition(":")
                if separator and value.startswith(" "):
                    value = value[1:]
                if field == "event":
                    event_name = value
                elif field == "data":
                    data_lines.append(value)

    async def _dispatch_sse_event(
        self,
        event_name: str | None,
        data_lines: list[str],
    ) -> None:
        if not data_lines:
            return

        payload = json.loads("\n".join(data_lines))
        if event_name == "gateway_status":
            values = payload.get("values")
            if isinstance(values, dict):
                await self._dispatch_gateway_values(values)
            return
        if event_name != "datapoint":
            return

        datapoint_id = str(payload.get("datapoint_id") or "")
        entries = self._datapoint_map.get(datapoint_id)
        if not entries:
            return

        value = extract_value({"value": payload})
        quality = "good" if payload.get("quality") == "good" else "bad"

        for binding in entries:
            await self._publish_binding_value(
                binding,
                value,
                quality=quality,
            )

    async def _dispatch_gateway_values(self, values: dict[str, Any]) -> None:
        for definition in GATEWAY_STATUS_DATAPOINTS:
            datapoint_id = definition["id"]
            if datapoint_id not in values:
                continue
            for binding in self._datapoint_map.get(datapoint_id, []):
                await self._publish_binding_value(
                    binding,
                    values[datapoint_id],
                    quality="good",
                )

    async def _publish_gateway_online(self, value: bool, *, quality: str) -> None:
        for binding in self._datapoint_map.get("gateway.online", []):
            await self._publish_binding_value(binding, value, quality=quality)

    async def _read_bound_values_once(self) -> None:
        for entries in list(self._datapoint_map.values()):
            for binding in entries:
                try:
                    value = await self.read(binding)
                    await self._publish_binding_value(
                        binding,
                        value,
                        quality="good",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "enocean-mqtt fallback read failed for binding %s: %s",
                        binding.id,
                        exc,
                    )
                    await self._publish_binding_value(
                        binding,
                        None,
                        quality="bad",
                    )

    async def _publish_binding_value(
        self,
        binding: Any,
        value: Any,
        *,
        quality: str,
    ) -> None:
        try:
            if binding.value_formula and value is not None:
                from obs.core.formula import apply_formula

                value = apply_formula(binding.value_formula, value)
            if binding.value_map:
                from obs.core.transformation import apply_value_map

                value = apply_value_map(value, binding.value_map)
        except Exception:
            logger.exception("enocean-mqtt value transform failed for binding %s", binding.id)
            quality = "bad"
            value = None

        await self._bus.publish(
            DataValueEvent(
                datapoint_id=binding.datapoint_id,
                value=value,
                quality=quality,
                source_adapter=self.adapter_type,
                binding_id=binding.id,
            ),
        )

    async def read(self, binding: Any) -> Any:
        if self._client is None:
            return None

        cfg = EnoceanMqttBindingConfig(**binding.config)
        if cfg.datapoint_id in GATEWAY_STATUS_IDS:
            response = await self._client.get("/api/v1/gateway/adapter-status")
            response.raise_for_status()
            values = response.json().get("values", {})
            if cfg.datapoint_id not in values:
                raise RuntimeError(f"enocean-mqtt status value is missing: {cfg.datapoint_id}")
            return values[cfg.datapoint_id]

        datapoint_id = quote(cfg.datapoint_id, safe="")
        response = await self._client.get(f"/api/v1/datapoints/{datapoint_id}/value")
        response.raise_for_status()
        return extract_value(response.json())

    async def write(self, binding: Any, value: Any) -> None:
        if self._client is None:
            raise RuntimeError("enocean-mqtt adapter is not connected")

        cfg = EnoceanMqttBindingConfig(**binding.config)
        datapoint_id = quote(cfg.datapoint_id, safe="")
        response = await self._client.post(
            f"/api/v1/datapoints/{datapoint_id}/value",
            json={"value": value},
        )
        response.raise_for_status()

    async def browse_devices(self, direction: str = "BOTH") -> list[dict[str, Any]]:
        if self._client is None:
            return []

        response = await self._client.get("/api/v1/devices")
        response.raise_for_status()
        devices = [_normalize_device(item) for item in _payload_items(response.json(), "devices")]
        if direction.upper() != "DEST":
            devices.append(
                {
                    "id": GATEWAY_DEVICE_ID,
                    "device_name": "enocean-mqtt Gateway",
                    "name": "enocean-mqtt Gateway",
                    "alias": None,
                    "eep": None,
                    "manufacturer": None,
                    "source_type": "gateway",
                    "virtual_device_id": None,
                    "readable": True,
                    "writable": False,
                    "datapoints_count": len(GATEWAY_STATUS_DATAPOINTS),
                }
            )
        return [device for device in devices if _matches_direction(device, direction)]

    async def browse_datapoints(self, device_id: str, direction: str = "SOURCE") -> list[dict[str, Any]]:
        if self._client is None:
            return []

        if device_id == GATEWAY_DEVICE_ID:
            return [
                dict(item)
                for item in GATEWAY_STATUS_DATAPOINTS
                if _matches_direction(item, direction)
            ]

        quoted_device_id = quote(device_id, safe="")
        response = await self._client.get(f"/api/v1/devices/{quoted_device_id}/datapoints")
        response.raise_for_status()
        items = [_normalize_datapoint(item, device_id=device_id) for item in _payload_items(response.json(), "datapoints")]
        return [item for item in items if _matches_direction(item, direction)]


def extract_value(payload: Any) -> Any:
    """Extract the semantic value from known enocean-mqtt value payload shapes."""
    if not isinstance(payload, dict):
        return payload

    if "value" in payload:
        value = payload["value"]
        if isinstance(value, dict) and "value" in value:
            return value["value"]
        return value

    datapoint = payload.get("datapoint")
    if isinstance(datapoint, dict) and "value" in datapoint:
        return datapoint["value"]

    return payload


def _payload_items(payload: Any, key: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        items = payload.get("items")
        if isinstance(items, list):
            return items
    return []


def _normalize_device(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "id": str(item),
            "device_name": str(item),
            "name": str(item),
            "datapoints_count": 0,
            "readable": True,
            "writable": False,
        }

    device_id = str(
        item.get("id")
        or item.get("device_id")
        or item.get("address")
        or item.get("external_id")
        or ""
    )
    device_name = str(
        item.get("device_name")
        or item.get("display_name")
        or item.get("name")
        or item.get("alias")
        or device_id
    )
    datapoints = item.get("datapoints")
    readable = _flag(item, "readable", "read", default=_direction_allows(item, {"read", "ro", "source"}))
    writable = _flag(item, "writable", "write", default=_direction_allows(item, {"write", "wo", "dest"}))
    if "direction" not in item and "access" not in item and "readable" not in item and "writable" not in item:
        if isinstance(datapoints, list):
            normalized_datapoints = [_normalize_datapoint(datapoint, device_id=device_id) for datapoint in datapoints]
            readable = any(datapoint["readable"] for datapoint in normalized_datapoints)
            writable = any(datapoint["writable"] for datapoint in normalized_datapoints)
        else:
            readable = True
            writable = False
    return {
        "id": device_id,
        "device_name": device_name,
        "name": device_name,
        "alias": item.get("alias"),
        "eep": item.get("eep") or item.get("profile"),
        "manufacturer": item.get("manufacturer"),
        "source_type": item.get("source_type"),
        "virtual_device_id": item.get("virtual_device_id"),
        "readable": readable,
        "writable": writable,
        "datapoints_count": len(datapoints) if isinstance(datapoints, list) else int(item.get("datapoints_count") or 0),
    }


def _normalize_datapoint(item: Any, device_id: str | None = None) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "id": str(item),
            "device_id": device_id,
            "name": str(item),
            "data_type": "UNKNOWN",
            "readable": True,
            "writable": False,
        }

    datapoint_id = str(item.get("id") or item.get("datapoint_id") or item.get("key") or "")
    raw_type = item.get("data_type") or item.get("type") or item.get("value_type")
    readable = _flag(item, "readable", "read", default=_direction_allows(item, {"read", "ro", "source"}))
    writable = _flag(item, "writable", "write", default=_direction_allows(item, {"write", "wo", "dest"}))
    if "direction" not in item and "access" not in item and "readable" not in item and "writable" not in item:
        readable = True
        writable = False
    return {
        "id": datapoint_id,
        "device_id": item.get("device_id") or device_id,
        "name": item.get("name") or item.get("display_name") or item.get("channel") or datapoint_id,
        "channel": item.get("channel"),
        "data_type": _obs_data_type(raw_type),
        "unit": item.get("unit"),
        "readable": readable,
        "writable": writable,
        "role": item.get("role") or item.get("semantic_role"),
        "value": extract_value(item) if "value" in item else None,
    }


def _flag(item: dict[str, Any], *keys: str, default: bool = False) -> bool:
    for key in keys:
        if key in item:
            return bool(item[key])
    return default


def _direction_allows(item: dict[str, Any], candidates: set[str]) -> bool:
    raw = item.get("direction", item.get("access", ""))
    if isinstance(raw, str):
        normalized = raw.lower()
        return normalized in candidates or normalized in {"both", "rw", "readwrite", "read_write"}
    if isinstance(raw, list):
        return any(str(value).lower() in candidates for value in raw)
    return False


def _matches_direction(item: dict[str, Any], direction: str) -> bool:
    normalized = direction.upper()
    if normalized == "DEST":
        return bool(item.get("writable"))
    if normalized == "BOTH":
        return bool(item.get("readable") or item.get("writable"))
    return bool(item.get("readable"))


def _obs_data_type(raw_type: Any) -> str:
    normalized = str(raw_type or "").strip().lower()
    if normalized in {"bool", "boolean"}:
        return "BOOLEAN"
    if normalized in {"int", "integer", "uint", "long"}:
        return "INTEGER"
    if normalized in {"float", "double", "number", "decimal"}:
        return "FLOAT"
    if normalized in {"str", "string", "text", "enum"}:
        return "STRING"
    if normalized in {"date"}:
        return "DATE"
    if normalized in {"time"}:
        return "TIME"
    if normalized in {"datetime", "timestamp"}:
        return "DATETIME"
    return "UNKNOWN"
