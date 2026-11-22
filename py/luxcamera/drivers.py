"""Drivers: load, resolve "extends", match a camera, validate against schema/driver.schema.json."""
from __future__ import annotations

import json
import re
from pathlib import Path

SCHEMA_VERSIONS = [1]
PROTOCOLS = ["panasonic-camcgi"]
CORE = Path(__file__).resolve().parents[2]  # the lumix-lux-core checkout


def load_drivers(folder=None) -> dict:
    """Every drivers/*.json -> {id: raw driver}."""
    out = {}
    for f in sorted(Path(folder or CORE / "drivers").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        out[d["id"]] = d
    return out


def load_schema(path=None) -> dict:
    return json.loads(Path(path or CORE / "schema" / "driver.schema.json").read_text(encoding="utf-8"))


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def resolve_driver(driver_id: str, drivers: dict) -> dict:
    """The driver with its "extends" chain merged in (objects merge, the rest replaces)."""
    chain, cur = [], driver_id
    while cur:
        if cur not in drivers:
            raise ValueError(f'Unknown driver "{cur}"')
        if cur in chain:
            raise ValueError(f'Driver "{driver_id}" extends itself')
        chain.append(cur)
        cur = drivers[cur].get("extends")
    out: dict = {}
    for cur in reversed(chain):
        out = _merge(out, drivers[cur])
    out["abstract"] = bool(drivers[driver_id].get("abstract"))
    out.pop("extends", None)
    out["chain"] = chain
    if out.get("schema") not in SCHEMA_VERSIONS:
        raise ValueError(f'Driver "{driver_id}" has schema {out.get("schema")}; this app understands {SCHEMA_VERSIONS}')
    if out.get("protocol") not in PROTOCOLS:
        raise ValueError(f'Driver "{driver_id}" needs protocol "{out.get("protocol")}"; this app needs an update')
    return out


def match_driver(drivers: dict, info: dict, protocol: str = "panasonic-camcgi") -> dict:
    """info: {modelNumber, friendlyName, capabilityModel}, any may be missing. The first concrete driver (by id) with a match
    key whose pattern matches the known value wins; else the family base in cautious mode (its features)."""
    ids = sorted(drivers)
    for i in ids:
        d = drivers[i]
        if d.get("abstract") or not d.get("match"):
            continue
        for key, pattern in d["match"].items():
            if info.get(key) is not None and re.search(pattern, info[key]):
                return {"driver": resolve_driver(i, drivers), "matched": True, "by": key}
    base = next((i for i in ids if drivers[i].get("abstract") and drivers[i].get("protocol") == protocol), None)
    if not base:
        raise ValueError(f'No driver for protocol "{protocol}"')
    return {"driver": resolve_driver(base, drivers), "matched": False, "by": None}


def _type(v):
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "null"


def _js(v) -> str:  # JSON.stringify spacing, so messages match the JS runtime
    return json.dumps(v, separators=(",", ":"))


def validate(value, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    """The subset of JSON Schema that schema/driver.schema.json uses. Returns "path: problem" strings, empty when valid."""
    root = root or schema
    if "$ref" in schema:
        return validate(value, root["$defs"][schema["$ref"].replace("#/$defs/", "")], root, path)
    errs: list[str] = []
    t = _type(value)
    want = schema.get("type")
    if want and not (want == t or (want == "number" and t == "integer")):
        return [f"{path}: should be {want}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: should be one of {_js(schema['enum'])}")
    if "pattern" in schema and t == "string" and not re.search(schema["pattern"], value):
        errs.append(f"{path}: doesn't match {schema['pattern']}")
    if "minimum" in schema and t in ("integer", "number") and value < schema["minimum"]:
        errs.append(f"{path}: should be >= {schema['minimum']}")
    if t == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append(f"{path}: should have at least {schema['minItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                errs += validate(v, schema["items"], root, f"{path}[{i}]")
    if t == "object":
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f'{path}: missing "{k}"')
        extra = schema.get("additionalProperties")
        for k, v in value.items():
            sub = schema.get("properties", {}).get(k)
            if sub:
                errs += validate(v, sub, root, f"{path}.{k}")
            elif extra is False:
                errs.append(f'{path}: unknown key "{k}"')
            elif isinstance(extra, dict):
                errs += validate(v, extra, root, f"{path}.{k}")
    return errs
