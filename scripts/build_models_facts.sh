#!/bin/bash
# catalog/models-facts.json from the reviewed branch of our models.dev fork (T-0116, AD132 ⑤), run by
# .github/workflows/models-facts.yml and by hand the same way:
#   bash scripts/build_models_facts.sh <fork checkout at dev>
# The fork's own generator, presets, overrides and schema (its frey/ directory), with pi's interface layer
# and OpenRouter's, LiteLLM's and aider's snapshots from its frey/sources/ (T-0173, T-0174; the copies Frey's
# repository keeps current); the table is written only
# when it fits the schema and has the presets Frey reads. Exit 1 otherwise (the old table stays).
set -euo pipefail
fork=${1:?usage: build_models_facts.sh <fork checkout>}
out=catalog/models-facts.json
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
python3 "$fork/frey/models_facts.py" "$fork" --out "$tmp" --upstream https://github.com/lannerk/models.dev \
  --vendors "$fork/frey/vendors.toml" --overrides "$fork/frey/overrides/models-facts-overrides.toml" \
  --pi "$fork/frey/sources/pi-ai.json" --sources "$fork/frey/sources"
python3 scripts/check_models_facts.py "$tmp" "$fork/frey/facts-1.schema.json"
mkdir -p catalog
mv "$tmp" "$out"
trap - EXIT
echo "build_models_facts: $out from $(git -C "$fork" rev-parse --short HEAD)"
