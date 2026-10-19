"""Focused-element capture via the Accessibility API.

Grabs what the user is actually looking at when the recording stops: the
window title, any selected text, and the tail of the text already in the
focused field. All of it is fed to the cleanup LLM so the rewrite can match
the surrounding tone/language (e.g. replying to an email in Spanish).

Everything is strictly best-effort and local: any failure returns an empty
dict and the pipeline continues without context. Secure fields are never
read. Uses the same Accessibility permission the paste synthesis already
requires — no new TCC grant.
"""

from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    AXUIElementCreateSystemWide,
)


def _copy(element, attribute):
    if element is None:
        return None
    try:
        error, value = AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception:
        return None
    return value if error == 0 else None


def _tail(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


def capture(max_chars: int = 1200) -> dict:
    """Return {window_title, selected_text, field_excerpt, field_role}.

    Missing pieces are simply absent from the dict.
    """
    out: dict[str, str] = {}
    try:
        system = AXUIElementCreateSystemWide()
        focused = _copy(system, "AXFocusedUIElement")

        role = _copy(focused, "AXRole")
        if isinstance(role, str):
            out["field_role"] = role

        if role != "AXSecureTextField":
            selected = _copy(focused, "AXSelectedText")
            if isinstance(selected, str) and selected.strip():
                out["selected_text"] = _tail(selected, max_chars)
            value = _copy(focused, "AXValue")
            if isinstance(value, str) and value.strip():
                # the cursor is almost always at the end while dictating,
                # so the tail is the relevant neighborhood
                out["field_excerpt"] = _tail(value, max_chars)

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is not None:
            ax_app = AXUIElementCreateApplication(app.processIdentifier())
            window = _copy(ax_app, "AXFocusedWindow")
            title = _copy(window, "AXTitle")
            if isinstance(title, str) and title.strip():
                out["window_title"] = title.strip()[:300]
    except Exception:
        return out
    return out
