#!/bin/zsh
# Rebuild the app runtime venv from the current source and restart the app.
# The app venv lives at ~/.local/share/localwhisper/venv (outside ~/Desktop,
# which is TCC-protected and unreadable to LocalWhisper.app).
set -e

PROJECT="${0:A:h}"   # directory this script lives in (the repo root)
VENV="$HOME/.local/share/localwhisper/venv"

uv pip install --python "$VENV/bin/python" --reinstall-package localwhisper "$PROJECT"

if pgrep -qf "localwhisper/venv/bin/localwhisper"; then
  pkill -f "localwhisper/venv/bin/localwhisper"
  sleep 1
  open "$HOME/Applications/LocalWhisper.app"
  echo "App updated and restarted."
else
  echo "App venv updated. Launch with: open ~/Applications/LocalWhisper.app"
fi
