#!/bin/bash
# DST-proof guard: only run if it's 15:00 ET (3:00 PM ET).
# Cron fires at 19:00 UTC + 20:00 UTC; this self-filters by ET hour.
HOUR_ET=$(TZ=America/New_York date +%H)
if [ "$HOUR_ET" != "15" ]; then
    exit 0
fi

cd /root/edge-stacker
source venv/bin/activate
set -a; source .env; set +a
OUT=$(python main.py --modules mlb_f5 --json-only 2>>/root/edge-stacker/logs/cron.log)
if [ -n "$OUT" ]; then
    curl -s -X POST https://vmi3157940.contaboserver.net/webhook/edge-stacker-mlb \
        -H "Content-Type: application/json" \
        -d "$OUT"
fi
