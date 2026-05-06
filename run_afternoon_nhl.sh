#!/bin/bash
# DST-proof guard: only run if it's exactly 16:00 ET (4:00 PM ET)
HOUR_ET=$(TZ=America/New_York date +%H)
if [ "$HOUR_ET" != "16" ]; then
    exit 0
fi

cd /root/edge-stacker
source venv/bin/activate
OUTPUT=$(python main.py --modules nhl_sog --json-only 2>>/root/edge-stacker/logs/cron.log)
if [ -n "$OUTPUT" ]; then
    curl -s -X POST https://vmi3157940.contaboserver.net/webhook/edge-stacker-nhl \
        -H "Content-Type: application/json" \
        -d "$OUTPUT"
fi
