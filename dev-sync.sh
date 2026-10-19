#!/bin/zsh
# Sync the DEV venv. Always use this instead of bare `uv sync`.
#
# iCloud syncs ~/Desktop and destroys anything venv-shaped in it: real venvs
# get UF_HIDDEN flags (Python then skips .pth files), and SYMLINKS GET
# DELETED (iCloud Drive does not support them). So the dev venv lives fully
# outside Desktop and nothing in the project references it. Invoke tools by
# absolute path:  ~/.local/share/localwhisper/dev-venv/bin/localwhisper
set -e
PROJECT="${0:A:h}"   # directory this script lives in (the repo root)
DEV_VENV="$HOME/.local/share/localwhisper/dev-venv"

cd "$PROJECT"
rm -rf .venv  # in case a bare `uv sync` recreated it under Desktop
UV_PROJECT_ENVIRONMENT="$DEV_VENV" uv sync --reinstall-package localwhisper "$@"
echo "dev venv synced: $DEV_VENV"
