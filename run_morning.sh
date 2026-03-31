#!/bin/bash
cd /root/edge-stacker
source venv/bin/activate
OUTPUT=$(python main.py --modules ncaab_kenpom,ncaab_conf_tourney --json-only 2>>/root/edge-stacker/logs/cron.log)
if [ -n "$OUTPUT" ]; then
    curl -s -X POST https://vmi3157940.contaboserver.net/webhook/edge-stacker-morning \
        -H "Content-Type: application/json" \
        -d "$OUTPUT"
fi
