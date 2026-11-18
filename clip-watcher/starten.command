#!/bin/bash
cd "$(dirname "$0")"
export CLIPWATCHER_LOOP=1
ARG=""
while true; do
  python3 app.py $ARG
  [ $? -eq 3 ] || break   # 3 = Update eingespielt, neu starten
  ARG="--neustart"
done
