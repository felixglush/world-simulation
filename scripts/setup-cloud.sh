#!/usr/bin/env bash
# Stop on errors, unset variables, or a failed command in a pipeline.
set -euo pipefail

# Resolve paths relative to this script, even when invoked outside the repository.
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

# Reuse uv only at the verified version; otherwise install it in a project-local venv.
uv_version="0.12.21"
if command -v uv >/dev/null 2>&1 && [[ "$(uv --version | awk '{print $2}')" == "$uv_version" ]]; then
    uv_bin="$(command -v uv)"
else
    python3 -m venv .codex-tools
    .codex-tools/bin/python -m pip install "uv==$uv_version"
    uv_bin="$project_root/.codex-tools/bin/uv"
fi

# Keep the dependency cache inside the workspace when no override is supplied.
export UV_CACHE_DIR="${UV_CACHE_DIR:-$project_root/.uv-cache}"
"$uv_bin" sync --frozen
"$uv_bin" run --frozen python scripts/check_environment.py
"$uv_bin" run --frozen ruff check .
"$uv_bin" run --frozen ruff format --check .
