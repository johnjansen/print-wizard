#!/bin/bash
# Polls origin/master and self-deploys when it moves, but only while the
# printer is idle (checked via this app's own /api/status). Run via cron;
# see ansible/provision.yml for the crontab entry (locking/logging live there,
# not here, so this script stays plain git+ansible logic).
set -euo pipefail

cd "$(dirname "$0")/.."

git fetch origin master -q
local_rev=$(git rev-parse HEAD)
remote_rev=$(git rev-parse origin/master)

if [ "$local_rev" = "$remote_rev" ]; then
    exit 0
fi

busy=$(curl -sf http://localhost:8765/api/status | python3 -c "import json,sys; print(json.load(sys.stdin)['busy'])" 2>/dev/null || echo "unknown")
if [ "$busy" != "False" ]; then
    echo "$(date -Iseconds) deploy deferred: printer busy=$busy, origin/master=$remote_rev"
    exit 0
fi

echo "$(date -Iseconds) deploying $remote_rev (was $local_rev)"
git reset --hard origin/master -q
ansible-playbook ansible/provision.yml -e @~/.wizard-secrets.yml
echo "$(date -Iseconds) deploy complete: $(git rev-parse HEAD)"
