#!/bin/zsh
# lw — force LocalWhisper on (and tell you why it was off).
#
# Why this exists: when dictation "stops working" there are four different
# causes and they all look identical from the outside (nothing happens when
# you double-tap). This command checks and fixes all four in order:
#
#   1. watchdog paused   -> ~/.config/localwhisper/watchdog-off exists, so
#                           nothing has been relaunching the app. Removed.
#   2. watchdog unloaded  -> the LaunchAgent got unloaded/never bootstrapped
#                           after a reboot. Re-bootstrapped + kickstarted.
#   3. app not running    -> no process = no hotkey (the Quartz event tap
#                           lives inside the app). Relaunched via `open`.
#   4. app wedged         -> process alive but the settings server on 43117
#                           is not answering, i.e. the run loop is stuck.
#                           Process existence alone does NOT mean healthy,
#                           which is why the watchdog can't catch this one.
#
# Launches with `open -g -a` ON PURPOSE (same reason as the watchdog): the
# TCC grants for mic / accessibility / input monitoring are attached to the
# app bundle identity, not to the venv interpreter. Launching the binary
# directly from a terminal silently loses them. -g = no focus steal.
#
# Canonical copy lives here; the running copy is ~/.local/bin/lw
# (NOT under ~/Desktop: TCC-protected + iCloud mangles files there).

APP="$HOME/Applications/LocalWhisper.app"
PROC_PAT="localwhisper/venv/bin/localwhispe[r]"   # see watchdog for the [r]
LOG="$HOME/Library/Logs/LocalWhisper.log"
WD_LOG="$HOME/Library/Logs/LocalWhisper-watchdog.log"
PAUSE="$HOME/.config/localwhisper/watchdog-off"
PLIST="$HOME/Library/LaunchAgents/com.localwhisper.watchdog.plist"
LABEL="com.localwhisper.watchdog"
PORT=43117

ok()   { print -P "%F{green}✓%f $*" }
warn() { print -P "%F{yellow}!%f $*" }
bad()  { print -P "%F{red}✗%f $*" }
info() { print -P "  $*" }

running() { pgrep -qf "$PROC_PAT" }
healthy() { curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/" }
pids()    { pgrep -f "$PROC_PAT" }

# Last state line the app wrote (recording / transcribing / idle …).
last_state() { grep -E '^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\]' "$LOG" 2>/dev/null | tail -1 }

# Refuse to kill mid-dictation unless forced: a restart there loses the audio.
busy() { last_state | grep -qE 'recording|transcribing' }

# Two-stage wait. The settings port answers well before the mlx model has
# loaded, and dictation is dead until the model is up, so reporting "ready"
# on the port alone would be a lie you'd only discover by double-tapping.
# Arg is the log line count taken BEFORE launching, so we only accept a
# fresh "model … ready" line and not the one from the previous run.
wait_ready() {
  local before=${1:-0} i
  for i in {1..45}; do
    running && healthy && break
    sleep 1
  done
  running && healthy || return 1
  for i in {1..60}; do
    tail -n "+$((before + 1))" "$LOG" 2>/dev/null | grep -q 'model .* ready' && return 0
    sleep 1
  done
  return 2   # process is up but the model never finished loading
}

log_lines() { wc -l < "$LOG" 2>/dev/null | tr -d ' ' || print 0 }

quit_app() {
  local p
  for p in $(pids); do kill "$p" 2>/dev/null; done
  local i
  for i in {1..10}; do running || return 0; sleep 0.5; done
  for p in $(pids); do kill -9 "$p" 2>/dev/null; done
  sleep 1
}

fix_watchdog() {
  if [[ -e "$PAUSE" ]]; then
    rm -f "$PAUSE"
    warn "watchdog was PAUSED (watchdog-off file) — that's why it stayed dead. Removed."
  fi
  if ! launchctl list | grep -q "$LABEL"; then
    launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null
    warn "watchdog LaunchAgent was not loaded — bootstrapped it."
  fi
  launchctl kickstart "gui/$UID/$LABEL" 2>/dev/null
}

start() {
  fix_watchdog
  if running && healthy; then
    ok "already running and answering on :$PORT"
    info "$(last_state)"
    return 0
  fi
  if running && ! healthy; then
    bad "process alive but WEDGED (no answer on :$PORT) — restarting it"
    restart force
    return $?
  fi
  print "starting LocalWhisper…"
  local before=$(log_lines)
  open -g -a "$APP" || { bad "open failed — is $APP there?"; return 1 }
  print "waiting for the model to load…"
  wait_ready "$before"
  case $? in
    0) ok "ready — 🎙 icon is in the menu bar, double-tap to dictate"
       info "$(last_state)" ;;
    2) warn "app is up but the model has not finished loading. Watch it with: lw log"
       return 1 ;;
    *) bad "did not come up in 45s. Last log lines:"
       tail -8 "$LOG"; return 1 ;;
  esac
}

restart() {
  if [[ "$1" != "force" ]] && busy; then
    bad "it is mid-dictation right now: $(last_state)"
    info "finish it, or force with: lw restart -f"
    return 1
  fi
  running && { print "stopping…"; quit_app }
  fix_watchdog
  print "starting…"
  local before=$(log_lines)
  open -g -a "$APP"
  print "waiting for the model to load…"
  wait_ready "$before"
  case $? in
    0) ok "restarted and ready — double-tap to dictate"
       info "$(last_state)" ;;
    2) warn "app is up but the model has not finished loading. Watch it with: lw log"
       return 1 ;;
    *) bad "did not come back in 45s. Last log lines:"
       tail -8 "$LOG"; return 1 ;;
  esac
}

stop() {
  if [[ "$1" != "force" ]] && busy; then
    bad "it is mid-dictation right now: $(last_state)"
    info "force with: lw stop -f"
    return 1
  fi
  # Pause first, otherwise the watchdog relaunches it within 10s.
  mkdir -p "${PAUSE:h}" && touch "$PAUSE"
  quit_app
  ok "stopped, and the watchdog is paused so it stays down"
  info "turn it back on with: lw"
}

status() {
  if running; then
    if healthy; then ok "app running (pid $(pids | tr '\n' ' ')) and answering on :$PORT"
    else bad "app running (pid $(pids | tr '\n' ' ')) but WEDGED — no answer on :$PORT. Run: lw restart"
    fi
  else
    bad "app NOT running — no hotkey. Run: lw"
  fi

  [[ -e "$PAUSE" ]] && warn "watchdog PAUSED (watchdog-off exists) — it will not come back on its own" \
                    || ok "watchdog active"
  launchctl list | grep -q "$LABEL" && ok "watchdog LaunchAgent loaded" \
                                    || bad "watchdog LaunchAgent NOT loaded. Run: lw"

  print "\nlast app activity:"
  grep -E '^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\]' "$LOG" 2>/dev/null | tail -5 | sed 's/^/  /'
  if [[ -s "$WD_LOG" ]]; then
    print "\nlast watchdog relaunches:"
    tail -3 "$WD_LOG" | sed 's/^/  /'
  fi
}

case "${1:-start}" in
  start|on|"")     start ;;
  restart|r)       [[ "$2" == "-f" || "$2" == "--force" ]] && restart force || restart ;;
  stop|off)        [[ "$2" == "-f" || "$2" == "--force" ]] && stop force || stop ;;
  status|st)       status ;;
  log)             tail -f "$LOG" ;;
  settings|ui)     open "http://127.0.0.1:$PORT/" ;;
  *)
    print "lw — control LocalWhisper dictation

  lw                force it on (fixes paused/unloaded watchdog, wedged app)
  lw status         what is actually wrong right now
  lw restart [-f]   stop and start again (-f: even mid-dictation)
  lw stop [-f]      stop it and keep it stopped
  lw log            follow the app log
  lw settings       open the settings UI"
    ;;
esac
