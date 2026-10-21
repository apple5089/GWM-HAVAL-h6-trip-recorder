"""Narrow discovery compatibility and value-free diagnostics for this validator.

Account envelopes, vehicle identities, platform routing and network policy still
use the pinned client. Only empty optional metadata and explicit boolean forms
are normalized. Unknown values remain errors rather than guessed identifiers.
"""
from collections import Counter
from collections.abc import Mapping

from gwm_client.china_client import (
    ChinaClient, _DISCOVERY_URL, _decode_g_app_envelope, _parse_vehicles, _property,
)
from gwm_client.errors import GwmClientError, GwmSchemaError


TEXT_FIELDS = frozenset({
    "appShowSeriesName", "vehicleNick", "modelName", "brandName", "otBrandName",
    "vtype", "vTypeName", "vehicleId", "belongPlatform",
})
FIELDS = tuple(sorted(TEXT_FIELDS | {"vin", "defaultVehicle", "vehicleNetworkType", "tankCapacity"}))
OPTIONAL_EMPTY = {name.casefold() for name in TEXT_FIELDS | {"vehicleNetworkType"}}


def shape(value):
    if value is None:
        return "null"
    if isinstance(value, str):
        return "empty_string" if value == "" else "string"
    if type(value) is bool:
        return "bool"
    if type(value) is int:
        return "int"
    if type(value) is float:
        return "float"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    return "other"


def parse_discovery(value, diagnostic):
    diagnostic.clear()
    diagnostic["container_type"] = shape(value)
    diagnostic["row_count"] = len(value) if isinstance(value, list) else None
    changes = Counter()
    if not isinstance(value, list):
        return _parse_vehicles(value)
    known_shapes = {name: set() for name in FIELDS}
    invalid_fields = set()
    # Diagnostic output is bounded, whitelist-only, and never contains values.
    for row in value[:20]:
        if not isinstance(row, Mapping):
            continue
        for name in FIELDS:
            raw = _property(row, name)
            known_shapes[name].add(shape(raw))
            try:
                probe = {"vin": "LGWTEST0000000001", name: raw}
                _parse_vehicles([probe])
            except (TypeError, ValueError, OverflowError, RecursionError):
                invalid_fields.add(name)
    diagnostic["field_types"] = {name: sorted(kinds) for name, kinds in known_shapes.items()}
    diagnostic["invalid_fields_before_normalization"] = sorted(invalid_fields)
    normalized = []
    for row in value:
        if not isinstance(row, Mapping):
            normalized.append(row)
            continue
        item = dict(row)
        for key, raw in row.items():
            if not isinstance(key, str):
                continue
            folded = key.casefold()
            if folded in OPTIONAL_EMPTY and raw == "":
                item[key] = None
                changes["empty_optional_metadata"] += 1
            elif folded == "defaultvehicle":
                if type(raw) is int and raw in (0, 1):
                    item[key] = bool(raw)
                    changes["explicit_boolean"] += 1
                elif isinstance(raw, str) and raw.casefold() in {"true", "false", "0", "1"}:
                    item[key] = raw.casefold() in {"true", "1"}
                    changes["explicit_boolean"] += 1
        normalized.append(item)
    diagnostic["normalizations"] = dict(changes)
    try:
        vehicles = _parse_vehicles(normalized)
    except (TypeError, ValueError, OverflowError, RecursionError):
        diagnostic["parsed"] = False
        raise
    diagnostic["parsed"] = True
    return vehicles


class DiagnosticChinaClient(ChinaClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.discovery_diagnostic = {}

    async def _acquire_vehicles_for_state(self, state, *, deadline):
        response = await self._send_locked(
            self._build_g_app_request(
                operation="acquire_vehicles", url=_DISCOVERY_URL,
                logical_body={"vehicleVersion": 13}, state=state, encrypt_body=False,
            ), deadline=deadline,
        )
        self.discovery_diagnostic = {"envelope": "pending"}
        try:
            data = _decode_g_app_envelope(response, operation="acquire_vehicles")
            value = _property(data, "acquireVehiclesList") if isinstance(data, Mapping) else None
            if value is None:
                value = data
            result = parse_discovery(value, self.discovery_diagnostic)
            self.discovery_diagnostic["envelope"] = "decoded"
            return result
        except GwmClientError as error:
            self.discovery_diagnostic["envelope_error"] = type(error).__name__
            raise
        except (RecursionError, OverflowError, TypeError, ValueError):
            self.discovery_diagnostic["envelope"] = "decoded"
            raise GwmSchemaError(operation="acquire_vehicles") from None
