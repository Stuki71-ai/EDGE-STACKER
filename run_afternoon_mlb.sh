#!/bin/bash
# DST-proof guard: only run if it's 16:30 ET (4:30 PM ET).
# Cron fires at 20:30 UTC + 21:30 UTC; this self-filters by ET hour.
HOUR_ET=$(TZ=America/New_York date +%H)
if [ "$HOUR_ET" != "16" ]; then
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
