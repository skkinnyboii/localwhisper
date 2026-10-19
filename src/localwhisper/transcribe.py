"""On-device speech-to-text.

Two engines:
  mlx            mlx-whisper on the Apple GPU (Metal). Default: 2-4x faster
                 than CTranslate2 on M-series. Model repos come from
                 mlx-community on HuggingFace.
  faster-whisper CTranslate2, CPU-only on macOS. Kept as the fallback; the
                 loader silently downgrades to it if MLX fails for any
                 reason (import error, download failure, unsupported model).
"""

import threading
from collections import Counter

import numpy as np

# Whisper free-runs into a repeated word or phrase when handed silence or
# room noise ("Men Men Men…", "Thank you. Thank you…"). An initial_prompt
# makes it WORSE: the glossary keeps the decoder confident, so whisper's own
# no_speech_threshold/logprob_threshold never fire and the loop is returned
# as if it were speech. These constants drive the post-hoc guard below.
_LOOP_MIN_WORDS = 12  # never second-guess a short, legitimate dictation
_LOOP_DOMINANCE = 0.5  # one token making up this much of a take = a loop
_LOOP_MAX_PHRASE = 6  # longest repeating phrase we look for
_LOOP_MIN_REPEATS = 4  # consecutive repeats before we call it a loop
_LOOP_MIN_KEEP_WORDS = 3  # a stray word or two ahead of a loop is noise too
_LOOP_STRIP = "\"'“”«»‘’.,!?;:¿¡…-–—()[]{}"

# faster-whisper aliases -> mlx-community repos (same weights, MLX format)
MLX_REPOS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "tiny": "mlx-community/whisper-tiny",
}


def _mlx_repo(model_name: str) -> str:
    if "/" in model_name:  # already a repo id or a local path
        return model_name
    return MLX_REPOS.get(model_name, f"mlx-community/whisper-{model_name}")


def _norm(word: str) -> str:
    return word.strip(_LOOP_STRIP).lower()


def _loop_start(norm: list[str]) -> int | None:
    """Index where a repeating tail block begins, or None if there is none.

    Finds the shortest phrase that repeats consecutively at the very end, so
    "…real speech. Thank you. Thank you. Thank you." keeps the real speech.
    """
    n = len(norm)
    for size in range(1, _LOOP_MAX_PHRASE + 1):
        if n < size * _LOOP_MIN_REPEATS:
            break
        block = norm[n - size :]
        start, repeats = n - size, 1
        while start - size >= 0 and norm[start - size : start] == block:
            start -= size
            repeats += 1
        if repeats >= _LOOP_MIN_REPEATS:
            return start
    return None


def strip_hallucinated_loop(text: str) -> str:
    """Drop repetition-loop garbage, keeping any real speech that preceded it.

    Returns "" when the whole take was a loop; callers treat that as
    "heard nothing" and paste nothing at all.
    """
    words = text.split()
    if len(words) < _LOOP_MIN_WORDS:
        return text  # too short to distinguish a loop from real emphasis
    norm = [_norm(w) for w in words]  # keep empties: indices stay aligned

    start = _loop_start(norm)
    kept = words[:start] if start is not None else words

    real = [w for w in (_norm(w) for w in kept) if w]
    if not real:
        return ""
    # "Do words words words…" — the leading token is decoder noise, not speech
    if start is not None and len(real) < _LOOP_MIN_KEEP_WORDS:
        return ""
    # a single token dominating the remainder means the loop was not just a
    # clean tail (it interleaved or led), so the take is not worth pasting
    if len(real) >= _LOOP_MIN_WORDS:
        if Counter(real).most_common(1)[0][1] >= _LOOP_DOMINANCE * len(real):
            return ""
    return " ".join(kept).strip()


class Transcriber:
    def __init__(
        self,
        model_name: str,
        compute_type: str = "int8",
        language: str | None = None,
        engine: str = "mlx",
    ):
        self.model_name = model_name
        self.compute_type = compute_type
        self.language = language
        self.engine = engine  # may downgrade to faster-whisper during load
        self._model = None  # faster-whisper model object (mlx caches internally)
        self.ready = threading.Event()
        self.load_error: str | None = None

    def load(self) -> None:
        try:
            if self.engine == "mlx":
                try:
                    self._load_mlx()
                except Exception as exc:
                    print(
                        f"mlx engine failed ({type(exc).__name__}: {exc}), "
                        "falling back to faster-whisper",
                        flush=True,
                    )
                    self.engine = "faster-whisper"
            if self.engine != "mlx":
                self._load_faster_whisper()
        except Exception as exc:
            self.load_error = f"{type(exc).__name__}: {exc}"
        finally:
            self.ready.set()

    def _load_mlx(self) -> None:
        import mlx_whisper

        self._mlx = mlx_whisper
        self._mlx_repo_id = _mlx_repo(self.model_name)
        # Warm-up run: downloads the weights on first use, loads them into
        # mlx_whisper's internal model cache, and triggers Metal kernel
        # compilation so the first real dictation pays none of it.
        silence = np.zeros(int(0.5 * 16000), dtype=np.float32)
        self._mlx.transcribe(silence, path_or_hf_repo=self._mlx_repo_id)

    def _load_faster_whisper(self) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self.model_name, device="auto", compute_type=self.compute_type
        )

    def load_async(self) -> None:
        threading.Thread(target=self.load, daemon=True, name="model-loader").start()

    def transcribe(self, audio, initial_prompt: str | None = None) -> str:
        """audio: 1-D float32 numpy array at 16 kHz, or a path to an audio file.

        initial_prompt biases the decoder toward known spellings (glossary).
        """
        self.ready.wait()
        if self.engine == "mlx":
            result = self._mlx.transcribe(
                audio,
                path_or_hf_repo=self._mlx_repo_id,
                language=self.language,
                initial_prompt=initial_prompt,
                condition_on_previous_text=False,  # fewer hallucination loops
            )
            return self._guard((result.get("text") or "").strip())
        if self._model is None:
            raise RuntimeError(f"whisper model failed to load: {self.load_error}")
        segments, _info = self._model.transcribe(
            audio,
            language=self.language,
            beam_size=1,
            vad_filter=True,
            initial_prompt=initial_prompt,
            condition_on_previous_text=False,
        )
        return self._guard(
            " ".join(segment.text.strip() for segment in segments).strip()
        )

    def _guard(self, text: str) -> str:
        kept = strip_hallucinated_loop(text)
        if kept != text:
            dropped = len(text) - len(kept)
            print(
                f"discarded {dropped} chars of repetition-loop hallucination",
                flush=True,
            )
        return kept
