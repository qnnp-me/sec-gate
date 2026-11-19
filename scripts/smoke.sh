#!/usr/bin/env bash
# 端到端冒烟：隔离 SEC_HOME + 独立端口，不起、不改任何既有实例。
#
# 断言：
#   1. sec --help 退出码 0
#   2. 隔离实例可用，url 指向 loopback
#   3. GET /healthz 返回 200 且 ok
#   4. 提交哨兵值后，结果页中哨兵 0 命中、[secret:DEMO] 至少 1 命中
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEC="$ROOT/sec"
SENTINEL='SENTINEL-DO-NOT-ECHO'
STATE_DIR="$(mktemp -d)"
PORT="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"

cleanup() {
  SEC_HOME="$STATE_DIR" SEC_PORT="$PORT" "$SEC" stop >/dev/null 2>&1 || true
  rm -rf "$STATE_DIR"
}
trap cleanup EXIT

echo "[1/6] sec --help"
"$SEC" --help >/dev/null
echo "ok"

echo "[2/6] 创建请求（SEC_HOME=$STATE_DIR SEC_PORT=$PORT）"
CREATE="$(SEC_HOME="$STATE_DIR" SEC_PORT="$PORT" "$SEC" request --env DEMO --ttl 60 -- printenv DEMO)"
ID="$(printf '%s' "$CREATE" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')"
URL="$(printf '%s' "$CREATE" | python3 -c 'import json, sys; print(json.load(sys.stdin)["url"])')"
echo "url=$URL"
case "$URL" in
  "http://127.0.0.1:$PORT/r/"*) echo "ok（loopback，未泄露对外前缀）" ;;
  *) echo "FAIL：未设 SEC_PUBLIC_BASE 时应回落到 loopback，实际 $URL"; exit 1 ;;
esac

echo "[3/6] GET /healthz"
curl -fsS "http://127.0.0.1:$PORT/healthz" | grep -q '"ok": true'
echo "ok"

echo "[4/6] 提交哨兵值"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  --data-urlencode "decision=run" --data-urlencode "v_DEMO=$SENTINEL" "$URL")"
test "$CODE" = "303"
echo "ok（http $CODE）"

echo "[5/6] 等结果并检查脱敏"
SEC_HOME="$STATE_DIR" SEC_PORT="$PORT" "$SEC" wait "$ID" --timeout 30 >/dev/null
BODY="$(curl -fsS "$URL")"
HITS="$(printf '%s' "$BODY" | grep -c "$SENTINEL" || true)"
MASKED="$(printf '%s' "$BODY" | grep -o '\[secret:DEMO\]' | wc -l)"
echo "sentinel_hits=$HITS secret_marker_hits=$MASKED"
test "$HITS" = "0"
test "$MASKED" -ge 1
echo "ok"

echo "[6/6] 停止隔离实例"
SEC_HOME="$STATE_DIR" SEC_PORT="$PORT" "$SEC" stop
echo "SMOKE=PASS"