#!/usr/bin/env bash
# Inference task: start SkyRL's inference servers (vLLM + router) and publish
# their URLs to the shared bucket, so the trainer can find them. Ports are
# picked dynamically by SkyRL, which is why we publish rather than hardcode.
#
# Usage: serve_and_publish.sh <endpoints.json> [skyrl serve overrides...]
set -euo pipefail
OUT=$1; shift
mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
LOG=$(mktemp)

uv run --isolated --extra fsdp -m skyrl.train.entrypoints.serve "$@" 2>&1 | tee "$LOG" &
SERVE_PID=$!

until grep -q "server_urls (control plane):" "$LOG"; do
  kill -0 $SERVE_PID 2>/dev/null || { echo "serve exited before becoming ready"; exit 1; }
  sleep 5
done
# Strip ANSI colors, then take the URL / list after the label (not after the
# last colon: the URLs themselves contain colons).
PROXY=$(sed 's/\x1b\[[0-9;]*m//g' "$LOG" | grep -o "proxy_url (data plane): *http[^ ]*" | tail -1 | grep -o "http[^ ]*")
SERVERS=$(sed 's/\x1b\[[0-9;]*m//g' "$LOG" | grep -o "server_urls (control plane): *\[.*\]" | tail -1 | grep -o "\[.*\]")
python3 - "$OUT" "$PROXY" "$SERVERS" <<'EOF'
import ast, json, sys
out, proxy, servers = sys.argv[1], sys.argv[2], ast.literal_eval(sys.argv[3])
tmp = out + '.tmp'
with open(tmp, 'w') as f:
    json.dump({'proxy_url': proxy, 'server_urls': list(servers)}, f)
import os; os.replace(tmp, out)
print('published', out, proxy, servers)
EOF
wait $SERVE_PID
