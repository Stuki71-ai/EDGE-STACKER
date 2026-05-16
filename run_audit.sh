#!/bin/bash
# EDGE STACKER VPS audit — fires 10 min after the NHL run (04:40 PM ET).
# DST-proof guard: cron fires at 20:40 UTC + 21:40 UTC; only the one that
# lands on 16:xx ET proceeds.
HOUR_ET=$(TZ=America/New_York date +%H)
if [ "$HOUR_ET" != "16" ]; then
    exit 0
fi

cd /root/edge-stacker
source venv/bin/activate
set -a; source .env; set +a
python audit.py >> /root/edge-stacker/logs/audit.log 2>&1
