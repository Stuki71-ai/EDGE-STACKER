#!/bin/bash
# CLV close capture — re-query Odds API for pending picks.
# Cron fires at 22:30 UTC (= 18:30 ET / 6:30 PM ET, ~30 min before earliest tip)
# and 23:30 UTC (= 19:30 ET, just before second wave). Last successful capture
# before tip-off becomes "the close" used in CLV grading.
cd /root/edge-stacker
source venv/bin/activate
set -a; source .env; set +a
python clv_capture.py >> logs/cron.log 2>&1

# After all games complete, grade the slate. Cron at 11:00 UTC (= 07:00 ET)
# next morning runs grade for yesterday's slate.
