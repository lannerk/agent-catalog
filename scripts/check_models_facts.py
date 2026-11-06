#!/usr/bin/env python3
"""check_models_facts.py <table.json> <facts-1.schema.json> — the catalog's facts table checked before it
is committed (T-0116): it fits the schema (jsonschema required here), comes from models.dev, and has at
least the presets and models a client expects (so a broken upstream week cannot publish an empty table)."""

import json
import sys

MIN_PRESETS = 10
MIN_MODELS = 500


def problems(table, schema):
    import jsonschema  # noqa: PLC0415

    out = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
           for e in jsonschema.Draft202012Validator(schema).iter_errors(table)][:20]
    providers = table.get("providers", {})
    count = sum(len(p.get("models", {})) for p in providers.values())
    if table.get("source") != "models.dev":
        out.append(f"source {table.get('source')!r}")
    if len(providers) < MIN_PRESETS or count < MIN_MODELS:
        out.append(f"only {len(providers)} presets and {count} models (at least {MIN_PRESETS} and {MIN_MODELS})")
    return out


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    with open(argv[1], encoding="utf-8") as f:
        table = json.load(f)
    with open(argv[2], encoding="utf-8") as f:
        schema = json.load(f)
    found = problems(table, schema)
    for p in found:
        print(f"check_models_facts: {p}", file=sys.stderr)
    if found:
        return 1
    count = sum(len(p["models"]) for p in table["providers"].values())
    print(f"check_models_facts: OK ({len(table['providers'])} presets, {count} models, upstream {table['upstream_commit'][:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
