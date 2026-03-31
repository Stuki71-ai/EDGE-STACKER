#!/bin/bash
cd /root/edge-stacker
source venv/bin/activate
OUTPUT=$(python main.py --modules nba_props,ncaaf_weather,ncaaf_bowls --json-only 2>>/root/edge-stacker/logs/cron.log)
if [ -n "$OUTPUT" ]; then
    curl -s -X POST https://vmi3157940.contaboserver.net/webhook/edge-stacker-afternoon \
        -H "Content-Type: application/json" \
        -d "$OUTPUT"
fi
