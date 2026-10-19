"""Orchestrator: hotkey -> record -> transcribe -> cleanup -> paste.

Run modes:
  localwhisper                    menu bar app
  localwhisper --no-ui            headless, states logged to the console
  localwhisper --check            permissions / LM Studio / model status
  localwhisper --download-model   pre-fetch the whisper model, then exit
  localwhisper --transcribe-file  run the pipeline on an audio file (no paste)
"""

import argparse
import subprocess
import sys
import threading
import time

import numpy as np

from . import axcontext
from . import cleanup as cleanup_mod
from . import history as history_mod
from .audio import SAMPLE_RATE, Recorder
from .config import CONFIG_PATH, load_config
from .context import context_instruction, frontmost_app, resolve_context
from .hotkey import DoubleTapListener
from .paste import paste_text
from .permissions import INSTRUCTIONS, report
from .transcribe import Transcriber

MIN_AUDIO_SECONDS = 0.35
# Room tone sits around 0.001 RMS, speech an order of magnitude above it.
# Handing whisper a take below this makes it invent a repetition loop, so
# discard first. Every take logs its RMS, so this stays tunable from real use.
SILENCE_RMS = 0.0015
# Below this, the glossary initial_prompt is withheld — see Pipeline._prompt_for.
GLOSSARY_MIN_SECONDS = 6.0
# A discarded take pastes nothing and hides the pill, which looks EXACTLY like
# a dead hotkey — that is how all three of these have been reported so far.
DISCARD_SOUND = "/System/Library/Sounds/Funk.aiff"


def _discard_cue() -> None:
    """Audible 'that take was binned, say it again'.

    Fires only on the discard paths, which are rare (four takes in a month),
    so this stays out of the way of normal dictation. afplay in a subprocess
    because this runs off the main thread and NSSound wants to be on it.
    """
    try:
        subprocess.Popen(
            ["afplay", "-v", "0.4", DISCARD_SOUND],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # a missing system sound must never break dictation
        pass

STATE_ICONS = {
    "loading": "⏳",
    "idle": "🎙",
    "recording": "🔴",
    "transcribing": "✍️",
    "error": "⚠️",
}


class Pipeline:
    def __init__(self, config: dict, on_state=None):
        self.config = config
        self.on_state = on_state or (lambda state, detail="": None)
        self.recorder = Recorder(max_seconds=config["max_recording_seconds"])
        self.transcriber = Transcriber(
            config["model"],
            compute_type=config["compute_type"],
            language=config["language"],
            engine=config["engine"],
        )
        self.state = "loading"
        self.last_result: str | None = None
        self.mode_override: str | None = None  # None = auto (per app)
        self.on_mode_change = None  # UI hook, set by the menu bar app
        vocabulary = config.get("vocabulary") or []
        self.initial_prompt = (
            f"Glossary: {', '.join(vocabulary)}." if vocabulary else None
        )
        self._toggle_lock = threading.Lock()

    def start(self) -> None:
        self.transcriber.load_async()
        threading.Thread(target=self._announce_ready, daemon=True).start()

    def _announce_ready(self) -> None:
        self.transcriber.ready.wait()
        if self.transcriber.load_error:
            self._set_state("error", self.transcriber.load_error)
        elif self.state == "loading":
            self._set_state(
                "idle",
                f"model {self.config['model']} ready "
                f"({self.transcriber.engine})",
            )

    def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        suffix = f" — {detail}" if detail else ""
        print(f"[{time.strftime('%H:%M:%S')}] {state}{suffix}", flush=True)
        self.on_state(state, detail)

    def toggle(self) -> None:
        with self._toggle_lock:
            if self.state == "recording":
                self._stop_and_process()
            elif self.state in ("idle", "loading"):
                # recording may start while the model still loads;
                # processing waits on the ready event
                self.recorder.start()
                self._set_state("recording")
            # in 'transcribing' or 'error' states taps are ignored

    def _stop_and_process(self) -> None:
        audio = self.recorder.stop()
        app_name, bundle_id = frontmost_app()  # capture the paste target now
        ax: dict = {}
        if self.config["context_capture"]["enabled"]:
            ax = axcontext.capture(self.config["context_capture"]["max_chars"])
        if audio is None or len(audio) < MIN_AUDIO_SECONDS * SAMPLE_RATE:
            self._set_state("idle", "recording too short, discarded")
            _discard_cue()
            return
        rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
        if rms < SILENCE_RMS:
            self._set_state("idle", f"silence (rms {rms:.4f}), discarded")
            _discard_cue()
            return
        self._set_state("transcribing", f"rms {rms:.4f}")
        threading.Thread(
            target=self._process, args=(audio, app_name, bundle_id, ax), daemon=True
        ).start()

    def _prompt_for(self, audio) -> str | None:
        """Glossary biasing, but only where it is safe.

        The initial_prompt is what lets whisper's repetition loops through: it
        keeps the decoder confident, so its own no_speech/logprob thresholds
        never fire and the loop comes back looking like speech. Short takes are
        where that goes wrong — every loop in history was under 3s of audio,
        while every take over 25s cleaned up fine. A few seconds of speech is
        also the least likely to need the glossary, so withholding it there
        costs almost nothing and lets whisper's own guards do their job.
        """
        if len(audio) < GLOSSARY_MIN_SECONDS * SAMPLE_RATE:
            return None
        return self.initial_prompt

    def _process(self, audio, app_name: str, bundle_id: str, ax: dict) -> None:
        try:
            step = time.monotonic()
            raw = self.transcriber.transcribe(
                audio, initial_prompt=self._prompt_for(audio)
            )
            transcribe_seconds = time.monotonic() - step
            if not raw:
                self._set_state("idle", "heard nothing")
                _discard_cue()
                return
            context_key = self.mode_override or resolve_context(
                self.config, app_name, bundle_id, ax.get("window_title", "")
            )
            final, was_cleaned, cleanup_seconds = raw, False, 0.0
            if self.config["cleanup"]["enabled"]:
                step = time.monotonic()
                final, was_cleaned = cleanup_mod.clean_transcript(
                    self.config["cleanup"],
                    raw,
                    app_name,
                    context_instruction(self.config, context_key),
                    extra_context=ax,
                    vocabulary=self.config.get("vocabulary") or [],
                )
                cleanup_seconds = time.monotonic() - step
            paste_text(
                final,
                restore_clipboard=self.config["paste"]["restore_clipboard"],
            )
            self.last_result = final
            if self.config["history"]["enabled"]:
                try:
                    history_mod.record(
                        app_name,
                        bundle_id,
                        context_key,
                        raw,
                        final,
                        was_cleaned,
                        len(audio) / SAMPLE_RATE,
                        self.config["model"],
                        engine=self.transcriber.engine,
                        transcribe_seconds=transcribe_seconds,
                        cleanup_seconds=cleanup_seconds,
                    )
                except Exception:
                    pass
            style = "cleaned" if was_cleaned else "raw"
            timing = f"whisper {transcribe_seconds:.1f}s"
            if self.config["cleanup"]["enabled"]:
                timing += f" + llm {cleanup_seconds:.1f}s"
            self._set_state(
                "idle", f"{style} → {app_name} ({context_key}) · {timing}"
            )
        except Exception as exc:
            self._set_state("error", str(exc))
            timer = threading.Timer(4.0, self._set_state, args=("idle",))
            timer.daemon = True
            timer.start()


def _warn_missing_permissions(perms: dict) -> None:
    for name, granted in perms.items():
        if granted is False:
            print(f"❗ {name} permission missing: {INSTRUCTIONS[name]}", flush=True)
    if any(granted is False for granted in perms.values()):
        print(
            "❗ Grant the permissions above, then relaunch LocalWhisper "
            "(or restart your terminal app if running from one).",
            flush=True,
        )


def run_headless(pipeline: Pipeline, listener: DoubleTapListener, hotkey_desc: str) -> None:
    def on_state(state: str, detail: str = "") -> None:
        icon = STATE_ICONS.get(state, "?")
        line = f"[{time.strftime('%H:%M:%S')}] {icon} {state}"
        if detail:
            line += f" — {detail}"
        print(line, flush=True)

    pipeline.on_state = on_state
    pipeline.start()
    listener.start()
    print(f"localwhisper listening: {hotkey_desc} to start/stop. Ctrl+C to quit.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nbye")


def run_menubar(pipeline: Pipeline, listener: DoubleTapListener, hotkey_desc: str) -> None:
    import subprocess

    import rumps
    from Foundation import NSOperationQueue

    class LocalWhisperApp(rumps.App):
        def __init__(self):
            super().__init__("LocalWhisper", title=STATE_ICONS["loading"])
            self._settings_window = None
            self.status_item = rumps.MenuItem("Starting…")
            hint_item = rumps.MenuItem(f"Hotkey: {hotkey_desc}")
            self.cleanup_item = rumps.MenuItem("", callback=self.toggle_cleanup)
            self._sync_cleanup_title()
            copy_item = rumps.MenuItem("Copy last transcript", callback=self.copy_last)
            settings_item = rumps.MenuItem("Settings…", callback=self.open_settings)
            config_item = rumps.MenuItem("Open config file", callback=self.open_config)
            self.mode_menu = rumps.MenuItem("Mode")
            self.mode_items = {}
            for key in [None, *sorted(pipeline.config["context_instructions"])]:
                label = "Auto (per app)" if key is None else key
                item = rumps.MenuItem(label, callback=self.pick_mode)
                self.mode_items[label] = (item, key)
                self.mode_menu.add(item)
            self._sync_mode_checks()
            self.menu = [
                self.status_item,
                hint_item,
                None,
                self.mode_menu,
                self.cleanup_item,
                copy_item,
                None,
                settings_item,
                config_item,
                None,
            ]

        def pick_mode(self, sender) -> None:
            pipeline.mode_override = self.mode_items[sender.title][1]
            self._sync_mode_checks()

        def _sync_mode_checks(self) -> None:
            for label, (item, key) in self.mode_items.items():
                item.state = 1 if key == pipeline.mode_override else 0
            suffix = pipeline.mode_override or "auto"
            self.mode_menu.title = f"Mode: {suffix}"

        def _sync_cleanup_title(self) -> None:
            enabled = pipeline.config["cleanup"]["enabled"]
            self.cleanup_item.title = (
                f"LLM cleanup: {'ON' if enabled else 'OFF'} (click to toggle)"
            )

        def toggle_cleanup(self, _sender) -> None:
            cleanup_config = pipeline.config["cleanup"]
            cleanup_config["enabled"] = not cleanup_config["enabled"]
            self._sync_cleanup_title()

        def copy_last(self, _sender) -> None:
            if pipeline.last_result:
                from .paste import set_clipboard

                set_clipboard(pipeline.last_result)

        def open_config(self, _sender) -> None:
            subprocess.run(["open", str(CONFIG_PATH)], check=False)

        def open_settings(self, _sender) -> None:
            # native window (menu callbacks run on the main thread);
            # falls back to the browser if WebKit is unavailable
            try:
                if self._settings_window is None:
                    from .settings_window import SettingsWindow

                    self._settings_window = SettingsWindow(settings.url)
                self._settings_window.show()
            except Exception as exc:
                print(f"native settings window failed ({exc}), "
                      "opening in browser", flush=True)
                subprocess.run(["open", settings.url], check=False)

        def set_state(self, state: str, detail: str = "") -> None:
            def apply():
                self.title = STATE_ICONS.get(state, STATE_ICONS["idle"])
                text = state if not detail else f"{state} — {detail}"
                self.status_item.title = text[:90]

            # UI updates must happen on the main thread
            NSOperationQueue.mainQueue().addOperationWithBlock_(apply)

    from .overlay import RecordingOverlay
    from .settings_server import start_server

    settings = start_server(pipeline)
    app = LocalWhisperApp()
    _install_reopen_handler(lambda: app.open_settings(None))
    # keep the menu bar checkmarks honest when the mode is changed from
    # the settings window (menu updates must run on the main thread)
    pipeline.on_mode_change = lambda: NSOperationQueue.mainQueue(
    ).addOperationWithBlock_(app._sync_mode_checks)
    overlay = RecordingOverlay()

    def on_state(state: str, detail: str = "") -> None:
        app.set_state(state, detail)
        if state == "recording":
            overlay.show_recording(pipeline.recorder)
        elif state == "transcribing":
            overlay.show_processing()
        else:
            overlay.hide()

    pipeline.on_state = on_state
    pipeline.start()
    listener.start()
    app.run()


def _install_reopen_handler(show_settings) -> None:
    """Dock-icon click on the running app opens the settings window.

    Clicking a running app's Dock tile sends applicationShouldHandleReopen:
    to the delegate. rumps owns the delegate class and does not implement
    it, so the method is grafted on before the delegate is instantiated.
    """
    import objc
    from rumps.rumps import NSApp as RumpsDelegate

    def applicationShouldHandleReopen_hasVisibleWindows_(self, sender, flag):
        print("dock reopen → settings window", flush=True)
        try:
            show_settings()
        except Exception as exc:
            print(f"reopen handler failed: {exc}", flush=True)
        return False  # handled; no default window restoration

    objc.classAddMethods(
        RumpsDelegate,
        [
            objc.selector(
                applicationShouldHandleReopen_hasVisibleWindows_,
                selector=b"applicationShouldHandleReopen:hasVisibleWindows:",
                signature=b"B@:@B",
            )
        ],
    )


def run_check(config: dict) -> None:
    marks = {True: "✅", False: "❌", None: "❓"}
    print("Permissions (granted to the app hosting this process):")
    for name, granted in report(prompt=False).items():
        line = f"  {marks[granted]} {name}"
        if granted is not True:
            line += f" — {INSTRUCTIONS[name]}"
        print(line)

    import requests

    base_url = config["cleanup"]["base_url"].rstrip("/")
    try:
        response = requests.get(f"{base_url}/models", timeout=3)
        response.raise_for_status()
        ids = [m["id"] for m in response.json().get("data", [])]
        pinned = config["cleanup"]["model"]
        using = pinned or (ids[0] if ids else "(none loaded)")
        print(f"  ✅ LLM server at {base_url} — cleanup model: {using}")
    except Exception as exc:
        print(
            f"  ❌ LLM server at {base_url} unreachable "
            f"({type(exc).__name__}) — cleanup will fall back to the raw transcript"
        )

    print(f"  Whisper model: {config['model']} via {config['engine']} "
          "(downloads on first use; pre-fetch with --download-model)")
    print(f"  Config: {CONFIG_PATH}")


def run_file(config: dict, path: str) -> None:
    started = time.time()
    print(f"Loading model {config['model']!r} (downloads on first use)…", flush=True)
    transcriber = Transcriber(
        config["model"],
        compute_type=config["compute_type"],
        language=config["language"],
        engine=config["engine"],
    )
    transcriber.load()
    if transcriber.load_error:
        sys.exit(f"model load failed: {transcriber.load_error}")
    print(
        f"Model ready in {time.time() - started:.1f}s "
        f"(engine: {transcriber.engine}). Transcribing…",
        flush=True,
    )

    vocabulary = config.get("vocabulary") or []
    initial_prompt = f"Glossary: {', '.join(vocabulary)}." if vocabulary else None
    step = time.time()
    raw = transcriber.transcribe(path, initial_prompt=initial_prompt)
    print(f"\nRaw transcript ({time.time() - step:.1f}s):\n  {raw}")

    if config["cleanup"]["enabled"]:
        app_name, bundle_id = frontmost_app()
        context_key = resolve_context(config, app_name, bundle_id)
        step = time.time()
        final, was_cleaned = cleanup_mod.clean_transcript(
            config["cleanup"], raw, app_name,
            context_instruction(config, context_key),
            vocabulary=vocabulary,
        )
        status = "cleaned" if was_cleaned else "LLM unavailable, raw kept"
        print(f"\nContext: {app_name} → {context_key}")
        print(f"Final ({status}, {time.time() - step:.1f}s):\n  {final}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="localwhisper",
        description="Free, fully local background dictation for macOS.",
    )
    parser.add_argument("--no-ui", action="store_true",
                        help="run headless (no menu bar icon)")
    parser.add_argument("--check", action="store_true",
                        help="check permissions, LLM server and model, then exit")
    parser.add_argument("--download-model", action="store_true",
                        help="download/load the whisper model, then exit")
    parser.add_argument("--transcribe-file", metavar="PATH",
                        help="transcribe an audio file through the pipeline "
                             "(prints result, no paste) and exit")
    parser.add_argument("--no-cleanup", action="store_true",
                        help="disable the LLM cleanup pass for this run")
    args = parser.parse_args()

    config = load_config()
    if args.no_cleanup:
        config["cleanup"]["enabled"] = False

    if args.check:
        run_check(config)
        return
    if args.download_model:
        print(f"Loading/downloading model {config['model']!r}…", flush=True)
        transcriber = Transcriber(
            config["model"], config["compute_type"], engine=config["engine"]
        )
        transcriber.load()
        if transcriber.load_error:
            sys.exit(f"FAILED: {transcriber.load_error}")
        print(f"Model ready (engine: {transcriber.engine}).")
        return
    if args.transcribe_file:
        run_file(config, args.transcribe_file)
        return

    perms = report(prompt=True)  # triggers the system prompts on first run
    _warn_missing_permissions(perms)

    pipeline = Pipeline(config)
    hotkey = config["hotkey"]
    listener = DoubleTapListener(
        hotkey["key"], hotkey["double_tap_window"], pipeline.toggle
    )
    hotkey_desc = f"double-tap {hotkey['key']}"
    if args.no_ui:
        run_headless(pipeline, listener, hotkey_desc)
    else:
        run_menubar(pipeline, listener, hotkey_desc)


if __name__ == "__main__":
    main()
