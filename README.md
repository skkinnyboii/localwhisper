# localwhisper

Free, fully local Superwhisper clone for macOS (Apple Silicon). Double-tap
Right Option, speak, double-tap again: the transcript is cleaned up by your
local LLM and pasted wherever your cursor is. No accounts, no telemetry, no
subscriptions, no network calls except your own LM Studio server.

Pipeline: global hotkey -> mic recording -> Whisper large-v3-turbo on device
(mlx-whisper on the Apple GPU, faster-whisper CPU fallback) -> optional
context-aware cleanup via LM Studio (localhost:1234, prompt adapted to the
frontmost app) -> Cmd+V paste at the cursor -> local SQLite history.

## Setup (one time)

```bash
git clone https://github.com/skkinnyboii/localwhisper
cd localwhisper

# 1. Install dependencies (PortAudio and ffmpeg come bundled in the wheels,
#    no brew packages needed)
uv sync

# 2. Pre-download the Whisper model (~1.6 GB, cached in ~/.cache/huggingface)
uv run localwhisper --download-model

# 3. Check status: permissions, LM Studio, model
uv run localwhisper --check
```

Permissions. macOS attributes grants to the app hosting the process, which
is your terminal app (Terminal, iTerm, Ghostty...), not Python. In System
Settings > Privacy & Security enable your terminal app under:

- Microphone (recording)
- Accessibility (synthesizing Cmd+V to paste)
- Input Monitoring (the global hotkey)

The first live run triggers the system prompts for you. After changing a
grant, fully restart the terminal app. If pasting silently does nothing,
Accessibility is the missing one; if the hotkey does nothing, it is Input
Monitoring.

## Run

Three ways, same tool:

```bash
open ~/Applications/LocalWhisper.app   # the app (Spotlight: "LocalWhisper")
localwhisper                           # global command, works from any directory
uv run localwhisper --no-ui            # dev mode from the project, headless logs
```

Menu bar states: 🎙 idle, 🔴 recording, ✍️ transcribing.

The app and the `localwhisper` command both run a dedicated venv at
`~/.local/share/localwhisper/venv`. That venv lives outside `~/Desktop` on
purpose: Desktop is a TCC-protected folder that LocalWhisper.app cannot
read. After changing code in this project, refresh it with:

```bash
./update-app.sh   # reinstalls the app venv and restarts the app
```

The app has its own permission identity, so macOS prompts for Microphone,
Accessibility and Input Monitoring under the name "LocalWhisper" on first
launch (independent of any grants your terminal has). Logs go to
`~/Library/Logs/LocalWhisper.log`. A second launch while one instance is
running exits quietly.

Usage: put the cursor where the text should go, double-tap Right Option,
speak, double-tap again. The final text is pasted at the cursor and your
previous clipboard is restored one second later. If LM Studio is not
running or times out, you always get the raw Whisper transcript instead
(the LLM pass never blocks dictation).

Test without the mic:

```bash
say -o /tmp/test.aiff "um so basically move the meeting to thursday"
uv run localwhisper --transcribe-file /tmp/test.aiff
```

## Config

`~/.config/localwhisper/config.json`, created on first run. Main knobs:

- `model`: any faster-whisper alias (`large-v3-turbo` default, `small` for
  speed, `distil-large-v3` English-only), HuggingFace CTranslate2 repo id,
  or local path.
- `language`: `null` autodetects per recording (fine for mixing Spanish and
  English); pin `"es"` or `"en"` to force it.
- `hotkey.key`: pynput key name (`alt_r` = Right Option; `f13`, `cmd_r`...).
- `cleanup.enabled` / `cleanup.base_url` / `cleanup.model`: the LLM pass.
  `model: null` uses whatever LM Studio lists first; pin e.g.
  `"qwen/qwen3-8b"` if you want a smaller, faster cleanup model than the
  30B coder.
- `app_contexts`: frontmost app -> format hint. Keys are bundle ids
  (`osascript -e 'id of app "Slack"'` to find one), plus `name:` prefixed
  keys that substring-match the app name. Extend freely.
- `context_instructions`: what each format hint tells the LLM.

Note: keys you delete from the config reappear because defaults are merged
underneath; to neutralize a mapping, point it at another context instead.

## History

Every dictation (timestamp, app, raw and final text) is stored locally:

```bash
sqlite3 ~/.config/localwhisper/history.db \
  "select ts, app_name, final_text from history order by id desc limit 10;"
```

Disable with `"history": {"enabled": false}`.

## Start at login (optional)

System Settings > General > Login Items & Extensions > add
`~/Applications/LocalWhisper.app`. Since the app has its own permission
identity, the grants persist across reboots with no extra setup.

## Uninstall

```bash
rm -rf <your localwhisper repo folder> ~/.config/localwhisper
rm -rf ~/Applications/LocalWhisper.app ~/.local/share/localwhisper
rm -f ~/.local/bin/localwhisper ~/Library/Logs/LocalWhisper.log
rm -rf ~/.cache/huggingface/hub/models--*whisper*   # model weights
```

## Possible upgrades (not built)

- Swift + WhisperKit rewrite for a single .app bundle with its own TCC
  identity.
