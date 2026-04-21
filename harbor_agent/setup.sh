#!/usr/bin/env bash
# Harbor setup script — runs inside the task container before the agent starts.
# Installs Python deps needed by the nessocode Harbor wrapper itself.
set -euo pipefail

uv venv /app --python python3.11 || uv venv /app
source /app/bin/activate

# Install harbor + the nessocode Harbor wrapper deps
uv pip install --quiet harbor nessocode
