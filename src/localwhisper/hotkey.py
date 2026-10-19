"""Global double-tap hotkey via a Quartz event tap (Input Monitoring).

Deliberately avoids pynput: its keyboard handling calls HIToolbox TSM/TIS
APIs from a background thread, which macOS now aborts with SIGTRAP
(dispatch_assert_queue). A listen-only CGEventTap reads raw keycodes and
never touches the text-services APIs, so it is safe off the main thread.
"""

import threading
import time
from typing import Callable

import Quartz

# virtual keycode + the modifier flag that marks it "pressed"
MODIFIER_KEYS = {
    "alt_r": (61, Quartz.kCGEventFlagMaskAlternate),
    "alt_l": (58, Quartz.kCGEventFlagMaskAlternate),
    "cmd_r": (54, Quartz.kCGEventFlagMaskCommand),
    "cmd_l": (55, Quartz.kCGEventFlagMaskCommand),
    "ctrl_r": (62, Quartz.kCGEventFlagMaskControl),
    "ctrl_l": (59, Quartz.kCGEventFlagMaskControl),
    "shift_r": (60, Quartz.kCGEventFlagMaskShift),
    "shift_l": (56, Quartz.kCGEventFlagMaskShift),
}

PLAIN_KEYS = {
    "f13": 105,
    "f14": 107,
    "f15": 113,
    "f16": 106,
    "f17": 64,
    "f18": 79,
    "f19": 80,
}


class DoubleTapListener:
    """Fires on_activate when the configured key is tapped twice quickly.

    Any other key pressed in between resets the sequence, so combos like
    Option+letter (accents, special characters) never trigger it.
    """

    def __init__(self, key_name: str, window: float, on_activate: Callable[[], None]):
        if key_name in MODIFIER_KEYS:
            self._keycode, self._flag_mask = MODIFIER_KEYS[key_name]
            self._is_modifier = True
        elif key_name in PLAIN_KEYS:
            self._keycode = PLAIN_KEYS[key_name]
            self._flag_mask = 0
            self._is_modifier = False
        else:
            known = ", ".join([*MODIFIER_KEYS, *PLAIN_KEYS])
            raise ValueError(f"unknown hotkey {key_name!r}; use one of: {known}")
        self._window = window
        self._on_activate = on_activate
        self._last_tap = 0.0
        self._tap = None

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="hotkey-tap").start()

    def _run(self) -> None:
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(
            Quartz.kCGEventFlagsChanged
        )
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._callback,
            None,
        )
        if self._tap is None:
            print(
                "❗ Could not create the keyboard event tap. Grant Input "
                "Monitoring to LocalWhisper (or your terminal app) and "
                "relaunch.",
                flush=True,
            )
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(self._tap, True)
        Quartz.CFRunLoopRun()

    def _callback(self, _proxy, event_type, event, _refcon):
        try:
            if event_type in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                Quartz.CGEventTapEnable(self._tap, True)
                return event
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            if event_type == Quartz.kCGEventFlagsChanged:
                if self._is_modifier and keycode == self._keycode:
                    if Quartz.CGEventGetFlags(event) & self._flag_mask:
                        self._register_tap()
                    # our key's release: keep the tap sequence alive
                else:
                    self._last_tap = 0.0
            elif event_type == Quartz.kCGEventKeyDown:
                if not self._is_modifier and keycode == self._keycode:
                    if not Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventAutorepeat
                    ):
                        self._register_tap()
                else:
                    self._last_tap = 0.0
        except Exception as exc:  # never let the tap thread die
            print(f"hotkey callback error: {exc}", flush=True)
        return event

    def _register_tap(self) -> None:
        now = time.monotonic()
        if now - self._last_tap <= self._window:
            self._last_tap = 0.0
            # keep the tap callback snappy; real work happens elsewhere
            threading.Thread(target=self._on_activate, daemon=True).start()
        else:
            self._last_tap = now
