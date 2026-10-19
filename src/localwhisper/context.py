"""Frontmost-app detection and format-hint resolution."""

from AppKit import NSWorkspace


def frontmost_app() -> tuple[str, str]:
    """Return (localized name, bundle id) of the frontmost application."""
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return "unknown", ""
    return str(app.localizedName() or "unknown"), str(app.bundleIdentifier() or "")


def resolve_context(
    config: dict, app_name: str, bundle_id: str, window_title: str = ""
) -> str:
    contexts = config["app_contexts"]
    context = None
    if bundle_id and bundle_id in contexts:
        context = contexts[bundle_id]
    else:
        lowered = app_name.lower()
        for key, candidate in contexts.items():
            if key.startswith("name:") and key[5:] in lowered:
                context = candidate
                break
    if context is None:
        context = config["default_context"]
    # a browser is not one context: Gmail is email, ChatGPT is a prompt...
    if context == "search_query_or_prose" and window_title:
        title = window_title.lower()
        for needle, refined in config["browser_title_contexts"].items():
            if needle in title:
                return refined
    return context


def context_instruction(config: dict, context_key: str) -> str:
    instructions = config["context_instructions"]
    return instructions.get(context_key) or instructions["prose"]
