"""macOS TCC permission checks.

Grants apply to the app hosting this process: when you run from a terminal,
that is Terminal.app / iTerm / Ghostty, not Python itself. After changing a
grant, restart the terminal app.
"""


def accessibility_trusted(prompt: bool = False) -> bool | None:
    try:
        from ApplicationServices import (
            AXIsProcessTrusted,
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
    except ImportError:
        return None
    if prompt:
        try:
            return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
        except Exception:
            pass
    return bool(AXIsProcessTrusted())


def input_monitoring_granted(prompt: bool = False) -> bool | None:
    try:
        from Quartz import CGPreflightListenEventAccess, CGRequestListenEventAccess
    except ImportError:
        return None
    try:
        if prompt:
            return bool(CGRequestListenEventAccess())
        return bool(CGPreflightListenEventAccess())
    except Exception:
        return None


INSTRUCTIONS = {
    "microphone": (
        "System Settings > Privacy & Security > Microphone: enable "
        "LocalWhisper (or your terminal app when running from a terminal)"
    ),
    "accessibility": (
        "System Settings > Privacy & Security > Accessibility: enable "
        "LocalWhisper (or your terminal app when running from a terminal); "
        "needed to synthesize Cmd+V"
    ),
    "input_monitoring": (
        "System Settings > Privacy & Security > Input Monitoring: enable "
        "LocalWhisper (or your terminal app when running from a terminal); "
        "needed for the global hotkey"
    ),
}


def report(prompt: bool = False) -> dict[str, bool | None]:
    from .audio import preflight_microphone

    mic_ok, _detail = preflight_microphone()
    return {
        "microphone": mic_ok,
        "accessibility": accessibility_trusted(prompt=prompt),
        "input_monitoring": input_monitoring_granted(prompt=prompt),
    }
