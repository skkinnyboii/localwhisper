"""Insert text at the cursor: clipboard + synthesized Cmd+V.

Requires the Accessibility permission for the synthetic keystroke to land.
Uses raw Quartz keyboard events (virtual keycode 9 = physical V key,
layout-independent) instead of pynput, whose layout lookups crash on
current macOS when called off the main thread.
"""

import threading
import time

import Quartz
from AppKit import NSPasteboard, NSPasteboardTypeString

_KEY_V = 9  # kVK_ANSI_V


def get_clipboard() -> str | None:
    return NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)


def set_clipboard(text: str) -> None:
    board = NSPasteboard.generalPasteboard()
    board.clearContents()
    board.setString_forType_(text, NSPasteboardTypeString)


def _synthesize_cmd_v() -> None:
    for pressed in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, _KEY_V, pressed)
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def paste_text(text: str, restore_clipboard: bool = True) -> None:
    previous = get_clipboard() if restore_clipboard else None
    set_clipboard(text)
    time.sleep(0.15)  # let the pasteboard settle before the paste lands
    _synthesize_cmd_v()
    if previous is not None:
        timer = threading.Timer(1.0, set_clipboard, args=(previous,))
        timer.daemon = True
        timer.start()
