#!/bin/zsh
# LocalWhisper watchdog: keeps the app process alive so the double-tap
# hotkey always works.
#
# Why this exists: the hotkey listener (Quartz event tap) lives INSIDE the
# app process. No app running = no hotkey, silently. The Login Item only
# fires at login, and this Mac goes weeks between reboots, so any mid-day
# quit (Cmd+Q is easy to hit now that LSUIElement=false puts it in the Dock
# and Cmd+Tab) left dictation dead until it was launched by hand.
#
# Launches via `open` ON PURPOSE: LaunchServices then attributes the process
# to the app bundle, so the existing TCC grants (mic / accessibility / input
# monitoring) keep applying. Running Contents/MacOS/LocalWhisper directly
# from launchd would attach permissions to the interpreter instead.
#
# -g keeps the relaunch in the background so it never steals focus mid-typing.
#
# Canonical copy lives here; the running copy is ~/.local/bin/localwhisper-watchdog
# (NOT under ~/Desktop: TCC-protected + iCloud mangles files there).
# Driven every 10s by ~/Library/LaunchAgents/com.localwhisper.watchdog.plist
#
# Escape hatch: `touch ~/.config/localwhisper/watchdog-off` to stop the
# relaunching (e.g. while rebuilding the app); remove the file to resume.

APP="$HOME/Applications/LocalWhisper.app"
LOG="$HOME/Library/Logs/LocalWhisper-watchdog.log"
PAUSE="$HOME/.config/localwhisper/watchdog-off"

[[ -e "$PAUSE" ]] && exit 0

# The [r] is deliberate: pgrep -f scans FULL command lines, so a plain
# pattern also matches any shell/editor that merely mentions the path (that
# false positive silently suppressed a relaunch during testing). The bracket
# makes the pattern match the real process but never a command line that
# quotes the pattern itself.
pgrep -qf "localwhisper/venv/bin/localwhispe[r]" && exit 0

print -r -- "[$(date '+%Y-%m-%d %H:%M:%S')] not running -> relaunching" >>"$LOG"
open -g -a "$APP"
