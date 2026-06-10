#!/usr/bin/env python3
"""
Validate Alire manifests against a JSON Schema.

Usage:
    check-schema.py <path> <schema-file>

Recursively finds every crate manifest under <path> (any *.toml file, the
index metadata file `index.toml` excluded) and validates it against the
given <schema-file>. The schema may be authored in JSON or YAML (YAML is a
JSON superset, so both load the same way).

Exit status:
    0  all manifests validate
    1  at least one manifest fails validation
    2  usage error, or the schema itself is invalid

Dependencies: jsonschema, PyYAML, tomllib (in requirements.txt)
"""

import os
import sys
import tomllib
from typing import Any, NoReturn

import yaml
from jsonschema import Draft202012Validator


def die(message: str, code: int = 2) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(code)


def load_schema(path: str) -> Draft202012Validator:
    """Load and self-check a JSON/YAML schema, returning a validator."""
    try:
        with open(path, encoding="utf-8") as f:
            schema = yaml.safe_load(f)
    except OSError as e:
        die(f"cannot read schema {path}: {e}")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as e:  # noqa: BLE001 - report any schema defect
        die(f"invalid schema {path}: {e}")
    return Draft202012Validator(schema)


def find_manifests(path: str) -> list[str]:
    """Return the sorted manifests under `path` (a file or a directory)."""
    if os.path.isfile(path):
        return [path]
    manifests = []
    for root, _dirs, files in os.walk(path):
        for name in files:
            if name.endswith(".toml") and name != "index.toml":
                manifests.append(os.path.join(root, name))
    return sorted(manifests)


def load_toml(path: str) -> dict[str, Any]:
    # tomllib requires a binary file object; it decodes UTF-8 itself
    with open(path, "rb") as f:
        return tomllib.load(f)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        die(f"usage: {os.path.basename(sys.argv[0])} <path> <schema-file>")
    path, schema_file = argv

    if not os.path.exists(path):
        die(f"path not found: {path}")

    validator = load_schema(schema_file)
    manifests = find_manifests(path)

    if not manifests:
        die(f"no manifests (*.toml) found under {path}")

    print(f"Validating {len(manifests)} manifest(s) under {path}")
    print(f"against schema {schema_file}\n")

    failed = 0
    for manifest in manifests:
        rel = os.path.relpath(manifest, path) if os.path.isdir(path) \
            else manifest
        try:
            data = load_toml(manifest)
        except Exception as e:  # noqa: BLE001 - malformed TOML is a failure
            failed += 1
            print(f"FAIL {rel}")
            print(f"     not valid TOML: {e}")
            continue

        errors = sorted(validator.iter_errors(data),
                        key=lambda e: list(e.path))
        if errors:
            failed += 1
            print(f"FAIL {rel}")
            for error in errors:
                loc = "/".join(str(p) for p in error.absolute_path) \
                    or "<root>"
                print(f"     @{loc}: {error.message}")

    print()
    if failed:
        print(f"FAILURE: {failed} of {len(manifests)} manifest(s) "
              f"failed schema validation")
        return 1
    print(f"SUCCESS: all {len(manifests)} manifest(s) validate")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
