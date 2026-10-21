#!/usr/bin/env python3
"""Report success responses whose OpenAPI schema is missing or too generic.

This is intentionally advisory while Phase 0c incrementally adds response models.
Pass --strict once every JSON success response is expected to have a concrete schema.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = ROOT / "apps" / "orchestrator" / "openapi.json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _schema_is_generic(
    schema: Any,
    components: dict[str, Any],
    seen_refs: frozenset[str] = frozenset(),
) -> bool:
    if not isinstance(schema, dict) or not schema:
        return True

    ref = schema.get("$ref")
    if isinstance(ref, str):
        prefix = "#/components/schemas/"
        if not ref.startswith(prefix) or ref in seen_refs:
            return False
        target = components.get(ref[len(prefix) :])
        return _schema_is_generic(target, components, seen_refs | {ref})

    schema_type = schema.get("type")
    properties = schema.get("properties")
    if schema_type == "object":
        if not isinstance(properties, dict) or not properties:
            # A typed additionalProperties schema is an intentional map contract;
            # an absent/true additionalProperties means arbitrary untyped JSON.
            additional = schema.get("additionalProperties")
            if isinstance(additional, dict):
                return _schema_is_generic(additional, components, seen_refs)
            return additional is not False
        return any(
            _schema_is_generic(value, components, seen_refs)
            for value in properties.values()
        )

    if schema_type == "array":
        return _schema_is_generic(schema.get("items"), components, seen_refs)

    for key in ("oneOf", "anyOf", "allOf"):
        members = schema.get(key)
        if isinstance(members, list) and any(
            _schema_is_generic(member, components, seen_refs) for member in members
        ):
            return True

    # OpenAPI schemas with only descriptive metadata but no shape constrain nothing.
    return schema_type is None and not any(
        key in schema for key in ("enum", "const", "pattern", "format")
    )


def audit_schema(document: dict[str, Any]) -> tuple[int, list[tuple[str, str, str, str]]]:
    paths = document.get("paths", {})
    components = document.get("components", {}).get("schemas", {})
    operation_count = 0
    findings: list[tuple[str, str, str, str]] = []

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_count += 1
            responses = operation.get("responses", {})
            for status_code, response in responses.items():
                if not status_code.startswith("2") or not isinstance(response, dict):
                    continue
                # 204/no-content endpoints intentionally have no response body contract.
                content = response.get("content", {})
                for media_type, media in content.items():
                    schema = media.get("schema") if isinstance(media, dict) else None
                    if _schema_is_generic(schema, components):
                        findings.append((method.upper(), path, status_code, media_type))
    return operation_count, findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero while any generic success-response schemas remain",
    )
    args = parser.parse_args()

    try:
        document = json.loads(args.schema.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"cannot read OpenAPI document {args.schema}: {exc}")

    operation_count, findings = audit_schema(document)
    print(f"OpenAPI operations audited: {operation_count}")
    print(f"Generic success response schemas: {len(findings)}")
    for method, path, status_code, media_type in findings:
        print(f"  {method:<7} {path} [{status_code} {media_type}]")
    if findings and args.strict:
        print("Run scripts/export_openapi.py after adding explicit response models.")
        return 1
    if not findings:
        print("[OK] All success response bodies have concrete schemas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
