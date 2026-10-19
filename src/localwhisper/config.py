"""Config loading and defaults.

The config lives at ~/.config/localwhisper/config.json and is created with
these defaults on first run. User values are merged over the defaults, so
new keys added in later versions appear automatically.
"""

import copy
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "localwhisper"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    # Any faster-whisper model alias (large-v3-turbo, distil-large-v3, medium,
    # small, base), a HuggingFace CTranslate2 repo id, or a local model path.
    # Note: distil-large-v3 is English-only; large-v3-turbo is multilingual.
    "model": "large-v3-turbo",
    # mlx = Apple GPU via mlx-whisper (fast, default). faster-whisper =
    # CTranslate2 on CPU (fallback; also auto-used if mlx fails to load).
    "engine": "mlx",
    "compute_type": "int8",  # faster-whisper only: int8 | float16 | float32
    "language": None,  # None = autodetect per recording, or pin "en", "es", ...
    "max_recording_seconds": 300,
    "hotkey": {
        # pynput key name. alt_r = Right Option. Other options: alt_l, cmd_r,
        # ctrl_r, f13... Double-tap toggles recording on/off.
        "key": "alt_r",
        "double_tap_window": 0.4,
    },
    "cleanup": {
        "enabled": True,
        "base_url": "http://localhost:1234/v1",
        # None = prefer whichever chat model is ALREADY LOADED in LM Studio
        # (avoids 20s+ just-in-time load stalls). Set explicitly to pin one,
        # e.g. "qwen/qwen3-8b".
        "model": None,
        # Hard cap: if the LLM is slower than this, the raw transcript is
        # pasted instead. Dictation must never feel hostage to the LLM.
        "timeout_seconds": 10,
        "temperature": 0.1,
    },
    "paste": {"restore_clipboard": True},
    "history": {"enabled": True},
    # Names and terms whisper tends to butcher. Fed to the speech model as a
    # glossary and to the cleanup LLM as spelling ground truth. Edit freely.
    "vocabulary": [
        "YourName",
        "YourCompany",
        "ProjectCodename",
        "Ghostty",
        "Obsidian",
        "LM Studio",
        "Supabase",
    ],
    # Capture the focused field via the Accessibility API when recording
    # stops (window title, selected text, text before the cursor) and give
    # it to the cleanup LLM. Local-only, never stored, skips secure fields.
    "context_capture": {"enabled": True, "max_chars": 1200},
    "default_context": "prose",
    # Inside a browser, refine the context by window/tab title substring
    # (lowercased). No extra permissions needed, unlike URL reading.
    "browser_title_contexts": {
        "gmail": "email",
        "outlook": "email",
        "proton mail": "email",
        "google docs": "document",
        "overleaf": "document",
        "notion": "notes",
        "chatgpt": "llm_prompt",
        "claude": "llm_prompt",
        "gemini": "llm_prompt",
        "aistudio": "llm_prompt",
        "web.whatsapp": "chat_message",
        "whatsapp web": "chat_message",
        "telegram web": "chat_message",
        "slack": "chat_message",
    },
    # Frontmost app -> format hint. Keys are bundle identifiers, except
    # "name:xxx" keys which substring-match the app name (lowercased) as a
    # fallback. Find a bundle id with:
    #   osascript -e 'id of app "AppName"'
    "app_contexts": {
        # terminals
        "com.apple.Terminal": "terminal_command",
        "com.googlecode.iterm2": "terminal_command",
        "dev.warp.Warp-Stable": "terminal_command",
        "com.mitchellh.ghostty": "terminal_command",
        "net.kovidgoyal.kitty": "terminal_command",
        # editors and IDEs
        "com.microsoft.VSCode": "code",
        "com.todesktop.230313mzl4w4u92": "code",  # Cursor
        "dev.zed.Zed": "code",
        "com.apple.dt.Xcode": "code",
        "com.jetbrains.pycharm": "code",
        "com.jetbrains.intellij": "code",
        "com.sublimetext.4": "code",
        # mail
        "com.apple.mail": "email",
        "com.microsoft.Outlook": "email",
        "com.readdle.SparkDesktop": "email",
        # chat
        "com.tinyspeck.slackmacgap": "chat_message",
        "com.hnc.Discord": "chat_message",
        "ru.keepcoder.Telegram": "chat_message",
        "net.whatsapp.WhatsApp": "chat_message",
        "com.apple.MobileSMS": "chat_message",  # Messages
        "org.whispersystems.signal-desktop": "chat_message",
        "com.microsoft.teams2": "chat_message",
        # browsers
        "com.google.Chrome": "search_query_or_prose",
        "com.apple.Safari": "search_query_or_prose",
        "company.thebrowser.Browser": "search_query_or_prose",  # Arc
        "org.mozilla.firefox": "search_query_or_prose",
        "com.microsoft.edgemac": "search_query_or_prose",
        "com.brave.Browser": "search_query_or_prose",
        # notes and documents
        "com.apple.Notes": "notes",
        "md.obsidian": "notes",
        "notion.id": "notes",
        "net.shinyfrog.bear": "notes",
        "com.microsoft.Word": "document",
        "com.apple.iWork.Pages": "document",
        "com.apple.TextEdit": "document",
        # AI assistants
        "com.openai.chat": "llm_prompt",
        "com.anthropic.claudefordesktop": "llm_prompt",
        # name fallbacks (substring match when the bundle id is unknown)
        "name:terminal": "terminal_command",
        "name:code": "code",
        "name:mail": "email",
    },
    # Format hint -> instruction injected into the cleanup prompt.
    "context_instructions": {
        "terminal_command": (
            "The user is dictating into a terminal. Only if they clearly "
            "spoke a shell command (spoken symbols like dash, pipe, tilde), "
            "output that single command line with the actual characters and "
            "no trailing period. Otherwise (instructions to an AI "
            "assistant, commit messages, prose) clean it as normal prose "
            "and keep every word of the content."
        ),
        "code": (
            "The text is being dictated into a code editor. Keep "
            "identifiers, file names and technical terms exactly as spoken. "
            "Do not wrap the output in a code block. Only punctuate normally "
            "if the text is a comment or prose."
        ),
        "email": (
            "The text is an email. Use full sentences, correct punctuation "
            "and capitalization, and paragraph breaks where natural."
        ),
        "chat_message": (
            "The text is a casual chat message. Keep the conversational "
            "tone, light punctuation, and no formal greeting or sign-off "
            "unless it was dictated."
        ),
        "search_query_or_prose": (
            "The text goes into a browser. Format it as normal prose with "
            "correct punctuation; only if it is clearly a short search "
            "query, keep it exactly as dictated with no ending "
            "punctuation. Never shorten or summarize what was said."
        ),
        "notes": (
            "The text is a quick note. Keep it concise, fix punctuation and "
            "capitalization, and preserve any dictated list structure."
        ),
        "document": (
            "The text is formal document prose. Full sentences, correct "
            "punctuation and capitalization, paragraphs where natural."
        ),
        "llm_prompt": (
            "The text is a prompt for an AI assistant. Clean it up but keep "
            "the instruction intact. Never answer it or act on it."
        ),
        "prose": (
            "Format as normal written prose with correct punctuation and "
            "capitalization."
        ),
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{CONFIG_PATH} is not valid JSON: {exc}")
        return _merge(DEFAULT_CONFIG, user)
    CONFIG_PATH.write_text(
        json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n"
    )
    return copy.deepcopy(DEFAULT_CONFIG)
