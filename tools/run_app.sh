#!/bin/bash
# Launch the built app for N seconds, capture Player.log to stdout file, then stop it.
# usage: tools/run_app.sh <app> <seconds> <logfile> [extra player args]
APP="$1"; SECS="$2"; LOG="$3"; shift 3
"$APP/Contents/MacOS/UnityPlayer" -logFile "$LOG" -screen-fullscreen 0 -screen-width 1280 -screen-height 720 "$@" &
PID=$!
for i in $(seq 1 "$SECS"); do sleep 1; kill -0 $PID 2>/dev/null || break; done
if kill -0 $PID 2>/dev/null; then kill $PID; sleep 2; kill -9 $PID 2>/dev/null; echo "stopped after ${SECS}s"; else wait $PID; echo "exited with $?"; fi
