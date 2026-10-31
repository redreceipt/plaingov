#!/bin/sh
# Deploy origin/main once its GitHub checks pass (plaingov-deploy.timer). Restarting cuts off running scrapes; they restart right away.
set -eu
cd "$(dirname "$0")"
git fetch -q origin main
new=$(git rev-parse origin/main)
[ "$new" = "$(git rev-parse HEAD)" ] && exit 0
curl -fsS "https://api.github.com/repos/redreceipt/plaingov/commits/$new/check-runs" | python3 -c '
import json, sys
runs = json.load(sys.stdin)["check_runs"]
sys.exit(not runs or any(r["conclusion"] != "success" for r in runs))' || { echo "waiting on checks for $new"; exit 0; }
git merge -q --ff-only "$new"
sudo cp ./*.service ./*.timer /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl restart plaingov-web plaingov-scrape
python3 plaingov.py site
echo "deployed $new"
