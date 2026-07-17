#!/usr/bin/env python3
"""Fail closed unless a Bazel execution log proves one expected remote action."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator


class ExecutionLogError(ValueError):
    pass


def iter_records(text: str) -> Iterator[dict[str, Any]]:
    decoder = json.JSONDecoder()
    offset = 0
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            return
        try:
            value, offset = decoder.raw_decode(text, offset)
        except json.JSONDecodeError as exc:
            raise ExecutionLogError(f"invalid execution JSON at byte {exc.pos}: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ExecutionLogError("execution log entries must be JSON objects")
        yield value


def _platform_properties(record: dict[str, Any]) -> dict[str, str]:
    platform = record.get("platform", {})
    if not isinstance(platform, dict):
        return {}
    properties = platform.get("properties", [])
    if not isinstance(properties, list):
        return {}
    result: dict[str, str] = {}
    for item in properties:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result[name] = value
    return result


def require_remote_action(
    records: list[dict[str, Any]],
    *,
    target: str,
    mnemonic: str,
    platform_property: tuple[str, str],
) -> dict[str, Any]:
    matches = [
        record
        for record in records
        if record.get("targetLabel") == target and record.get("mnemonic") == mnemonic
    ]
    if len(matches) != 1:
        raise ExecutionLogError(
            f"expected exactly one {mnemonic} record for {target}, found {len(matches)}"
        )

    record = matches[0]
    expected = {
        "runner": "remote",
        "cacheHit": False,
        "status": "",
        "exitCode": 0,
        "remotable": True,
        "cacheable": False,
        "remoteCacheable": False,
    }
    proto_defaults: dict[str, object] = {
        "runner": "",
        "cacheHit": False,
        "status": "",
        "exitCode": 0,
        "remotable": False,
        "cacheable": False,
        "remoteCacheable": False,
    }
    for field, value in expected.items():
        actual = record.get(field, proto_defaults[field])
        if actual != value:
            raise ExecutionLogError(
                f"{mnemonic} {target} field {field} must be {value!r}, got {actual!r}"
            )

    property_name, property_value = platform_property
    actual_property = _platform_properties(record).get(property_name)
    if actual_property != property_value:
        raise ExecutionLogError(
            f"{mnemonic} {target} platform property {property_name} must be "
            f"{property_value!r}, got {actual_property!r}"
        )
    return record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--mnemonic", required=True)
    parser.add_argument("--platform-property", required=True, metavar="NAME=VALUE")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        property_name, separator, property_value = args.platform_property.partition("=")
        if not separator or not property_name or not property_value:
            raise ExecutionLogError("--platform-property must be NAME=VALUE")
        records = list(iter_records(args.log.read_text(encoding="utf-8")))
        require_remote_action(
            records,
            target=args.target,
            mnemonic=args.mnemonic,
            platform_property=(property_name, property_value),
        )
    except (OSError, ExecutionLogError) as exc:
        raise SystemExit(f"execution proof failed: {exc}") from exc

    print(
        f"REMOTE ACTION PROOF: PASS ({args.mnemonic} {args.target}; "
        f"{args.platform_property}; uncached)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
