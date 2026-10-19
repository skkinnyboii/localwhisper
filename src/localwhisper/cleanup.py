"""Optional transcript cleanup through a local OpenAI-compatible server.

Designed for LM Studio at localhost:1234. Strictly best-effort: any failure
(server down, timeout, bad response) falls back to the raw transcript so
dictation never blocks on the LLM.
"""

import re
import time

import requests

SYSTEM_PROMPT = (
    "You are a dictation cleanup engine. You receive raw speech-to-text "
    "output and return the SAME text, cleaned: fix punctuation and "
    "capitalization, remove filler words (um, uh, you know) and false "
    "starts. Keep everything else the speaker said, in their own words and "
    "their own language. Never summarize, condense, shorten or paraphrase. "
    "Never answer questions, never comment on the text, never add content, "
    "never translate. The output must say exactly what the speaker said, "
    "minus fillers. Reply with the cleaned transcript and nothing else."
)

_MODEL_CACHE_TTL = 60.0  # seconds; load state on the server changes over time
_model_cache: dict[str, tuple[float, str]] = {}

_EMBEDDING_HINTS = ("embed", "embedding", "rerank")


class NoLoadedModel(RuntimeError):
    """LM Studio is up but holds no LLM in memory.

    Asking it to infer anyway triggers a just-in-time load — ~20s+ for a 30B
    — which always blows the cleanup timeout AND makes the user wait out the
    whole thing before the raw text pastes. Bail in milliseconds instead.
    """


def _server_model(base_url: str, timeout: float) -> str:
    """Pick a cleanup model when none is pinned in the config.

    Prefers a model that LM Studio reports as ALREADY LOADED (its /api/v0
    endpoint exposes load state) so a dictation never has to wait ~20s for
    a just-in-time model load. Falls back to the first /v1/models entry on
    non-LM Studio servers.
    """
    cached = _model_cache.get(base_url)
    if cached and time.monotonic() - cached[0] < _MODEL_CACHE_TTL:
        return cached[1]

    choice = None
    is_lmstudio = False
    root = base_url[: -len("/v1")] if base_url.endswith("/v1") else base_url
    try:  # LM Studio native API, includes "state": "loaded" | "not-loaded"
        response = requests.get(f"{root}/api/v0/models", timeout=timeout)
        response.raise_for_status()
        models = response.json().get("data") or []
        is_lmstudio = True
        loaded = [
            m["id"]
            for m in models
            if m.get("state") == "loaded"
            and m.get("type") in ("llm", "vlm")
            and not any(hint in m["id"].lower() for hint in _EMBEDDING_HINTS)
        ]
        if loaded:
            choice = loaded[0]
    except Exception:
        pass

    # Deliberately NOT cached: the moment he loads a model in LM Studio the
    # next dictation should pick it up, not wait out a stale negative.
    if choice is None and is_lmstudio:
        raise NoLoadedModel("LM Studio has no model loaded; skipping cleanup")

    if choice is None:
        response = requests.get(f"{base_url}/models", timeout=timeout)
        response.raise_for_status()
        data = response.json().get("data") or []
        candidates = [
            m["id"]
            for m in data
            if not any(hint in m["id"].lower() for hint in _EMBEDDING_HINTS)
        ]
        if not candidates:
            raise RuntimeError("no usable models on the server")
        choice = candidates[0]

    _model_cache[base_url] = (time.monotonic(), choice)
    return choice


def _is_thinking_qwen(model: str) -> bool:
    model = model.lower()
    return "qwen3" in model and "coder" not in model and "instruct" not in model


def _strip_wrapping(text: str) -> str:
    # thinking models (qwen3 etc.) may emit a reasoning block first
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fence = re.match(r"^```[a-zA-Z]*\n(.*?)\n?```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def _context_block(extra_context: dict | None, vocabulary: list | None) -> str:
    """Optional prompt sections: surrounding text and spelling glossary."""
    parts = []
    extra = extra_context or {}
    if extra.get("window_title"):
        parts.append(f"Active window: {extra['window_title']}")
    if extra.get("selected_text"):
        parts.append(
            "The user has this text SELECTED, and the dictation will replace "
            f"it (match its language and tone):\n«{extra['selected_text']}»"
        )
    elif extra.get("field_excerpt"):
        parts.append(
            "Text already in the field before the cursor (the dictation "
            "continues it; match its language and tone):\n"
            f"«{extra['field_excerpt']}»"
        )
    if vocabulary:
        parts.append(
            "Proper nouns that may occur — if the transcript contains a "
            "close mishearing of one, spell it exactly like this: "
            + ", ".join(vocabulary)
        )
    if not parts:
        return ""
    return "\n\nContext (do not include any of this in the output):\n" + "\n".join(
        parts
    )


def clean_transcript(
    cleanup_config: dict,
    raw_text: str,
    app_name: str,
    instruction: str,
    extra_context: dict | None = None,
    vocabulary: list | None = None,
) -> tuple[str, bool]:
    """Return (final_text, was_cleaned). Falls back to raw_text on any failure."""
    base_url = cleanup_config["base_url"].rstrip("/")
    timeout = cleanup_config["timeout_seconds"]
    try:
        model = cleanup_config["model"] or _server_model(
            base_url, timeout=min(timeout, 5)
        )
        prompt = (
            "Clean up this raw dictation transcript for the following "
            f"context: the user is dictating into {app_name}. {instruction} "
            "Fix punctuation and capitalization, remove filler words (um, "
            "uh, like, you know), do not add content, do not answer "
            "questions, just reformat the transcript for this context."
            f"{_context_block(extra_context, vocabulary)}\n\n"
            f"Transcript: {raw_text}"
        )
        if _is_thinking_qwen(model):
            prompt += "\n/no_think"  # qwen3 soft switch: skip the <think> pass
        response = requests.post(
            f"{base_url}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": cleanup_config["temperature"],
                # ~1 token per 4 chars, with generous slack for punctuation
                "max_tokens": max(300, len(raw_text)),
                "stream": False,
            },
            timeout=(3, timeout),
        )
        response.raise_for_status()
        cleaned = _strip_wrapping(
            response.json()["choices"][0]["message"]["content"] or ""
        )
        # distrust empty or runaway output (model answered instead of
        # cleaning) and heavy shrinkage (model summarized instead of
        # cleaning — filler removal alone never halves a long dictation)
        if not cleaned or len(cleaned) > max(80, 3 * len(raw_text)):
            return raw_text, False
        if len(raw_text) > 60 and len(cleaned) < 0.5 * len(raw_text):
            return raw_text, False
        return cleaned, True
    except Exception:
        return raw_text, False
