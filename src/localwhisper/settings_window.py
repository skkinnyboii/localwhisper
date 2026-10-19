"""Native settings window.

A standard macOS NSWindow owned by the app, hosting a WKWebView pointed at
the local settings server (127.0.0.1). Same UI as the browser version, but
it looks and behaves like a normal app window: own title bar, Cmd+W to
close, reopenable from the menu bar. Main-thread only — call show() from a
menu callback (rumps invokes those on the main thread).
"""

from AppKit import (
    NSApp,
    NSAppearance,
    NSBackingStoreBuffered,
    NSMakeRect,
    NSScreen,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorMoveToActiveSpace,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSURL, NSURLRequest
from WebKit import WKWebView, WKWebViewConfiguration

WIDTH = 920
HEIGHT = 760


class SettingsWindow:
    def __init__(self, url: str):
        self.url = url
        self._window = None

    def show(self) -> None:
        if self._window is None:
            self._build()
        self._window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)  # LSUIElement apps need this

    def _build(self) -> None:
        area = NSScreen.mainScreen().visibleFrame()
        x = area.origin.x + (area.size.width - WIDTH) / 2.0
        y = area.origin.y + (area.size.height - HEIGHT) / 2.0
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, WIDTH, HEIGHT),
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered,
            False,
        )
        window.setTitle_("LocalWhisper Settings")
        window.setReleasedWhenClosed_(False)  # close hides; show() reopens
        window.setMinSize_((680, 480))
        # Appear on whatever Space/desktop is active when shown, and follow
        # into full-screen apps, instead of staying on the Space the app was
        # first launched in (which makes it look like nothing opened).
        window.setCollectionBehavior_(
            NSWindowCollectionBehaviorMoveToActiveSpace
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        dark = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
        if dark is not None:
            window.setAppearance_(dark)  # title bar matches the dark page

        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, WIDTH, HEIGHT),
            WKWebViewConfiguration.alloc().init(),
        )
        webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        webview.loadRequest_(
            NSURLRequest.requestWithURL_(NSURL.URLWithString_(self.url))
        )
        window.setContentView_(webview)
        self._window = window
