#!/bin/bash
# CLV grade — runs next morning to grade yesterday's slate.
cd /root/edge-stacker
source venv/bin/activate
set -a; source .env; set +a
python clv_grade.py >> logs/clv_grade.log 2>&1
