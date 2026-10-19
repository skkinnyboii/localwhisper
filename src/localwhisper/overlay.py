"""Floating waveform pill, superwhisper-style.

A borderless, non-activating glass panel at the bottom center of the
screen: live audio bars while recording, a pulsing wave while
transcribing. It never takes focus, so the paste target keeps the cursor.
All AppKit work is dispatched to the main thread; the public methods are
safe to call from any thread.
"""

import math
import time

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSMakeRect,
    NSPanel,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSScreen,
    NSStatusWindowLevel,
    NSTimer,
    NSView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSOperationQueue

WIDTH = 240
HEIGHT = 52
BAR_COUNT = 26
FPS = 30.0


class _WaveView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(_WaveView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.mode = "record"
        self.recorder = None
        return self

    def _levels(self):
        if self.mode == "process":
            t = time.monotonic()
            return [
                0.30 + 0.22 * math.sin(t * 6.0 + i * 0.55)
                for i in range(BAR_COUNT)
            ]
        recorder = self.recorder
        raw = list(recorder.levels)[-BAR_COUNT:] if recorder is not None else []
        # speech RMS sits around 0.01-0.15; lift it into a lively 0-1 range
        scaled = [min(1.0, (value * 12.0) ** 0.7) for value in raw]
        return [0.0] * (BAR_COUNT - len(scaled)) + scaled

    def drawRect_(self, rect):
        bounds = self.bounds()
        width, height = bounds.size.width, bounds.size.height
        gap = 3.0
        bar_width = (width - gap * (BAR_COUNT - 1)) / BAR_COUNT
        middle = height / 2.0
        NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.92).set()
        x = 0.0
        for level in self._levels():
            bar_height = max(3.0, level * (height - 6.0))
            bar = NSMakeRect(x, middle - bar_height / 2.0, bar_width, bar_height)
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar, bar_width / 2.0, bar_width / 2.0
            ).fill()
            x += bar_width + gap


class RecordingOverlay:
    def __init__(self):
        self._panel = None
        self._view = None
        self._timer = None

    # ----- public, thread-safe -----

    def show_recording(self, recorder) -> None:
        self._on_main(lambda: self._show("record", recorder))

    def show_processing(self) -> None:
        self._on_main(lambda: self._show("process", None))

    def hide(self) -> None:
        self._on_main(self._hide)

    # ----- main thread only -----

    @staticmethod
    def _on_main(fn) -> None:
        NSOperationQueue.mainQueue().addOperationWithBlock_(fn)

    def _ensure_panel(self) -> None:
        if self._panel is not None:
            return
        screen = NSScreen.mainScreen()
        area = screen.visibleFrame()
        x = area.origin.x + (area.size.width - WIDTH) / 2.0
        y = area.origin.y + 28.0
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, WIDTH, HEIGHT),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setIgnoresMouseEvents_(True)
        panel.setHasShadow_(True)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        effect = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, WIDTH, HEIGHT)
        )
        effect.setMaterial_(NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        effect.layer().setCornerRadius_(HEIGHT / 2.0)
        effect.layer().setMasksToBounds_(True)
        view = _WaveView.alloc().initWithFrame_(
            NSMakeRect(18, 8, WIDTH - 36, HEIGHT - 16)
        )
        effect.addSubview_(view)
        panel.setContentView_(effect)
        self._panel = panel
        self._view = view

    def _show(self, mode: str, recorder) -> None:
        self._ensure_panel()
        self._view.mode = mode
        self._view.recorder = recorder
        if self._timer is None:
            self._timer = NSTimer.timerWithTimeInterval_repeats_block_(
                1.0 / FPS, True, lambda _t: self._view.setNeedsDisplay_(True)
            )
            NSRunLoop.mainRunLoop().addTimer_forMode_(
                self._timer, NSRunLoopCommonModes
            )
        self._panel.setAlphaValue_(1.0)
        self._panel.orderFrontRegardless()

    def _hide(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._panel is not None:
            self._panel.orderOut_(None)
