# localwhisper — developer notes

Free, fully local background dictation for macOS (Apple Silicon). This file is
the maintainer's map of the architecture and the hard-won gotchas. See
README.md for setup and usage.

Pipeline: double-tap Right Option -> record -> Whisper on device -> optional
context-aware cleanup via LM Studio (localhost:1234) -> Cmd+V paste at cursor.
No cloud calls of any kind except the local LLM.

## Architecture (one module per concern, all in localwhisper/)

- `app.py`: Pipeline orchestrator, CLI flags, headless and menu bar modes.
  Every state change is printed with a timestamp (lands in the app log),
  including per-stage timings (whisper Xs + llm Ys). Menu bar has a Mode
  submenu (auto per app, or force a context) and Settings….
- `transcribe.py`: two engines behind one class: mlx (mlx-whisper, Apple
  GPU, default, ~1s for a 10s clip) and faster-whisper (CPU fallback,
  auto-used if MLX fails to load). Vocabulary goes in as initial_prompt.
- `axcontext.py`: Accessibility capture at recording stop: window title,
  selected text, tail of the focused field. Best-effort, skips secure
  fields, feeds the cleanup prompt.
- `settings_server.py` + `settings_page.html`: local settings UI served at
  http://127.0.0.1:43117. ThreadingHTTPServer bound to 127.0.0.1 with a
  Host-header guard. GET/POST /api/config (live-apply;
  model/engine/language/hotkey changes report restart_required),
  /api/status, /api/history, /api/llm-models.
- `settings_window.py`: menu bar -> Settings… opens the UI in a native
  NSWindow hosting a WKWebView (dark titlebar, Cmd+W closes, reopenable;
  `setReleasedWhenClosed(False)` is what makes reopening safe). Falls back
  to the browser if WebKit import fails. Main-thread only.
- `overlay.py`: superwhisper-style floating pill (NSPanel, non-activating,
  bottom center): live waveform bars while recording, sine pulse while
  transcribing. Menu bar mode only (headless has no main run loop).
- `audio.py`: sounddevice recorder, 16 kHz mono float32.
- `cleanup.py`: LM Studio chat call; strictly best-effort, always falls
  back to the raw transcript; strips `<think>` blocks. When no model is
  pinned it prefers a model LM Studio reports as already loaded. Adds
  `/no_think` for thinking qwen3 variants.
- `context.py`: NSWorkspace frontmost app -> format hint; inside browsers
  the window/tab title refines it (gmail -> email etc. via
  `browser_title_contexts`).
- `hotkey.py`: Quartz CGEventTap double-tap listener (any other key resets
  the tap). No pynput (see gotcha).
- `paste.py`: NSPasteboard + synthetic Cmd+V via Quartz CGEventPost (vk 9),
  clipboard restored after 1 s. No pynput.
- `config.py`: defaults + deep merge; the app->context map and the
  per-context prompt instructions both live in the user config.
- `permissions.py`: TCC checks (mic / accessibility / input monitoring).

## Gotchas

- **pynput is banned here.** The moment real key events flowed, the app died
  with SIGTRAP in HIToolbox `TSMGetInputSourceProperty` via
  `dispatch_assert_queue`: macOS aborts TSM/TIS text-services calls from
  background threads, and pynput makes them. No Python traceback; only
  `~/Library/Logs/DiagnosticReports/python3.*.ips` told the story. Replaced
  with a listen-only Quartz CGEventTap (raw keycodes) and CGEventPost. If a
  keyboard lib is needed again, verify it makes zero TIS calls off the main
  thread.

- **The package lives in `src/localwhisper` on purpose.** With a flat layout,
  older `uv run localwhisper` executed the package directory instead of the
  console script and crashed. src layout removes the name collision. Do not
  flatten it. If editable imports break after moving files:
  `uv cache clean localwhisper && uv sync --reinstall-package localwhisper`.

- **Venv location matters if the project lives under iCloud Drive.** iCloud
  sync sets UF_HIDDEN on files in venvs, and Python 3.12+ silently skips
  hidden `.pth` files, so the editable install vanishes with
  `ModuleNotFoundError`. It also deletes symlinks. Keep the venv outside any
  iCloud-synced folder and point `UV_PROJECT_ENVIRONMENT` at it (see
  `dev-sync.sh`).

- **`LocalWhisper.app` cannot read a venv under a TCC-protected folder**
  (e.g. Desktop), so it uses a separate runtime venv outside the project
  (refreshed by `update-app.sh`).

- **TCC prompts show as "python 3.x", not "LocalWhisper"** when launched via
  a shell script that execs python: macOS attributes permissions to the uv
  managed interpreter. Grants die if that interpreter is upgraded; re-grant
  then. A bundled app fixes the naming. Also: launch via `open`/Dock/
  Spotlight, not by running `Contents/MacOS/LocalWhisper` from a terminal
  (that inherits the terminal's TCC identity).

- **Mic grants attach to the terminal app hosting the process, not Python.**
  Mic streams open fine WITHOUT the grant (silence), so the mic check can be
  a false positive.

- **`distil` Whisper models are English-only.** Default stays
  `large-v3-turbo` with `language=null` autodetect for mixed-language users.

- **Summarization guard.** The cleanup LLM must never shorten a dictation.
  If a context instruction demands "a single command line", prose gets
  summarized into commands. The default instructions only command-ify when
  clearly dictated as such, the system prompt forbids summarizing, and
  `cleanup.py` has a hard guard: raw > 60 chars and cleaned < 50% of raw ->
  paste raw.

- **Whisper repetition loops on short/quiet takes.** The vocabulary
  `initial_prompt` keeps the decoder confident, so whisper's own
  no_speech/logprob thresholds never fire and it loops. Mitigations:
  an RMS silence gate in `app.py` (`SILENCE_RMS`) before transcription, and
  `strip_hallucinated_loop()` in `transcribe.py` applied to both engines.
  The glossary prompt is withheld below `GLOSSARY_MIN_SECONDS` so the takes
  most likely to loop do not get it. Discards play an audible cue, because
  a silent discard looks identical to a dead hotkey.

- **Prefer a loaded cleanup model.** LM Studio JIT-loading a model can take
  ~20 s. `cleanup.py` bails instantly (`NoLoadedModel`) when the native API
  reports zero loaded LLMs, so the next dictation picks up a model the
  moment one is loaded.

## Watchdog + autostart

The hotkey listener lives inside the app process, so no app = no hotkey,
silently. A LaunchAgent (`~/Library/LaunchAgents/`) runs `scripts/watchdog.sh`
every 10 s to relaunch a dead app. It relaunches with `open -g -a` on purpose:
LaunchServices attributes the process to the app bundle so TCC grants keep
applying, and `-g` avoids stealing focus. Pause it with
`touch ~/.config/localwhisper/watchdog-off` (e.g. while rebuilding); delete
the file to resume. Restarts are logged to
`~/Library/Logs/LocalWhisper-watchdog.log`.

`scripts/lw.sh` (installed as `lw`) fixes the four different causes that all
look like "it's off": watchdog paused, LaunchAgent unloaded, app not running,
and app running-but-wedged (settings port dead). Readiness is two-stage: wait
for the port, then wait for a fresh `model … ready` log line, since dictation
is dead until the model finishes loading.
