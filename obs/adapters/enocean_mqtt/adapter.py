"""enocean-mqtt REST API adapter.

Consumes the enocean-mqtt API v1 as a read-only semantic source. EnOcean,
EEP and datapoint semantics stay in enocean-mqtt; this adapter only maps API
datapoint values into open bridge DataValueEvents.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field, field_validator

from obs.adapters.base import AdapterBase
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent

logger = logging.getLogger(__name__)


class EnoceanMqttAdapterConfig(BaseModel):
    base_url: str = Field(default="http://localhost:8001")
    token: str | None = Field(default=None, json_schema_extra={"format": "password"})
    poll_interval: float = Field(default=10.0, ge=1.0)
    timeout: float = Field(default=10.0, ge=1.0)

    @field_validator("base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError("base_url must not be empty")
        return normalized


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
        self._poll_tasks: list[asyncio.Task] = []

    async def connect(self) -> None:
        self._cfg = EnoceanMqttAdapterConfig(**self._config)
        headers = {}
        if self._cfg.token:
            headers["Authorization"] = f"Bearer {self._cfg.token}"

        self._client = httpx.AsyncClient(
            base_url=self._cfg.base_url,
            headers=headers,
            timeout=self._cfg.timeout,
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
        for task in self._poll_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._poll_tasks.clear()

        if self._client is not None:
            await self._client.aclose()
            self._client = None

        await self._publish_status(False, "Disconnected", code="disconnected")

    async def _on_bindings_reloaded(self) -> None:
        for task in self._poll_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._poll_tasks.clear()

        if self._client is None or not self.connected:
            return

        for binding in self._bindings:
            if binding.direction not in ("SOURCE", "BOTH"):
                continue
            try:
                EnoceanMqttBindingConfig(**binding.config)
            except Exception:
                logger.warning("Invalid enocean-mqtt binding config for %s — skipped", binding.id)
                continue
            self._poll_tasks.append(
                asyncio.create_task(
                    self._poll_loop(binding),
                    name=f"enocean-mqtt-poll-{binding.id}",
                ),
            )

        logger.info("enocean-mqtt adapter: %d poll task(s) started", len(self._poll_tasks))

    async def _poll_loop(self, binding: Any) -> None:
        while True:
            try:
                value = await self.read(binding)
                if binding.value_formula:
                    from obs.core.formula import apply_formula

                    value = apply_formula(binding.value_formula, value)
                if binding.value_map:
                    from obs.core.transformation import apply_value_map

                    value = apply_value_map(value, binding.value_map)
                await self._bus.publish(
                    DataValueEvent(
                        datapoint_id=binding.datapoint_id,
                        value=value,
                        quality="good",
                        source_adapter=self.adapter_type,
                        binding_id=binding.id,
                    ),
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("enocean-mqtt poll failed for binding %s: %s", binding.id, exc)
                await self._bus.publish(
                    DataValueEvent(
                        datapoint_id=binding.datapoint_id,
                        value=None,
                        quality="bad",
                        source_adapter=self.adapter_type,
                        binding_id=binding.id,
                    ),
                )
            await asyncio.sleep(self._cfg.poll_interval)

    async def read(self, binding: Any) -> Any:
        if self._client is None:
            return None

        cfg = EnoceanMqttBindingConfig(**binding.config)
        datapoint_id = quote(cfg.datapoint_id, safe="")
        response = await self._client.get(f"/api/v1/datapoints/{datapoint_id}/value")
        response.raise_for_status()
        return extract_value(response.json())

    async def write(self, binding: Any, value: Any) -> None:
        logger.debug("enocean-mqtt write ignored — REST adapter is read-only (binding %s)", binding.id)

    async def browse_devices(self, direction: str = "BOTH") -> list[dict[str, Any]]:
        if self._client is None:
            return []

        response = await self._client.get("/api/v1/devices")
        response.raise_for_status()
        devices = [_normalize_device(item) for item in _payload_items(response.json(), "devices")]
        return [device for device in devices if _matches_direction(device, direction)]

    async def browse_datapoints(self, device_id: str, direction: str = "SOURCE") -> list[dict[str, Any]]:
        if self._client is None:
            return []

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
    if normalized in {"str", "string", "text"}:
        return "STRING"
    if normalized in {"date"}:
        return "DATE"
    if normalized in {"time"}:
        return "TIME"
    if normalized in {"datetime", "timestamp"}:
        return "DATETIME"
    return "UNKNOWN"
