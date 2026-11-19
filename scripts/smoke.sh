#!/usr/bin/env bash
# 端到端冒烟：隔离 SEC_HOME + 独立端口，只通过 SEC_BIN 指定的可执行文件操作。
#
# 断言：
#   1. sec --help 退出码 0
#   2. 隔离实例可用，url 指向 loopback
#   3. GET /healthz 返回 200 且 ok
#   4. 无 token 调 /api/request → 403
#   5. 值超过 16KB → 413
#   6. 提交哨兵值 → 303
#   7. 结果页里哨兵 0 命中、[secret:DEMO] 至少 1 命中
#   8. GET 无效 id → 404
#   9. 重放同一 id → 410
#  10. stop 清干净
#
# 输出只允许：步骤标签、url=、healthz_ok=、http_code=、status_state=、
# sentinel_hits=/secret_marker_hits=、结尾 SMOKE=PASS。不打印 API 原始 JSON。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEC="${SEC_BIN:-sec}"
SENTINEL='SENTINEL-DO-NOT-ECHO'
STATE_DIR="$(mktemp -d)"
PORT="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
BASE="http://127.0.0.1:$PORT"

cleanup() {
  SEC_HOME="$STATE_DIR" SEC_PORT="$PORT" "$SEC" stop >/dev/null 2>&1 || true
  rm -rf "$STATE_DIR"
}
trap cleanup EXIT

echo "[1/10] sec --help"
"$SEC" --help >/dev/null

echo "[2/10] 创建请求（SEC_HOME=$STATE_DIR SEC_PORT=$PORT）"
CREATE="$(SEC_HOME="$STATE_DIR" SEC_PORT="$PORT" "$SEC" request --env DEMO --ttl 60 -- printenv DEMO)"
ID="$(printf '%s' "$CREATE" | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')"
URL="$(printf '%s' "$CREATE" | python3 -c 'import json, sys; print(json.load(sys.stdin)["url"])')"
echo "url=$URL"
case "$URL" in
  "$BASE/r/"*) ;;
  *) echo "FAIL：未设 SEC_PUBLIC_BASE 时应回落到 loopback，实际 $URL"; exit 1 ;;
esac

echo "[3/10] GET /healthz"
HEALTH="$(curl -fsS "$BASE/healthz" | python3 -c 'import json, sys; print(str(json.load(sys.stdin).get("ok")).lower())')"
echo "healthz_ok=$HEALTH"
test "$HEALTH" = "true"

echo "[4/10] 无 token 调 /api/request"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
  --data '{"argv":["true"],"envs":["DEMO"]}' "$BASE/api/request")"
echo "http_code=$CODE"
test "$CODE" = "403"

echo "[5/10] 超大值"
BIG="$(python3 -c 'print("X" * 17000)')"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  --data-urlencode "decision=run" --data-urlencode "v_DEMO=$BIG" "$URL")"
echo "http_code=$CODE"
test "$CODE" = "413"

echo "[6/10] 提交哨兵值"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  --data-urlencode "decision=run" --data-urlencode "v_DEMO=$SENTINEL" "$URL")"
echo "http_code=$CODE"
test "$CODE" = "303"

echo "[7/10] 等结果并检查脱敏"
WAIT="$(SEC_HOME="$STATE_DIR" SEC_PORT="$PORT" "$SEC" wait "$ID" --timeout 30)"
STATE="$(printf '%s' "$WAIT" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("state") or "")')"
echo "status_state=$STATE"
test "$STATE" = "ran"
BODY="$(curl -fsS "$URL")"
HITS="$(printf '%s' "$BODY" | grep -c "$SENTINEL" || true)"
MASKED="$(printf '%s' "$BODY" | grep -o '\[secret:DEMO\]' | wc -l)"
echo "sentinel_hits=$HITS secret_marker_hits=$MASKED"
test "$HITS" = "0"
test "$MASKED" -ge 1

echo "[8/10] GET 无效 id"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/r/aaaaaaaaaaaaaaaaaaaaaaaa")"
echo "http_code=$CODE"
test "$CODE" = "404"

echo "[9/10] 重放同一 id"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  --data-urlencode "decision=run" --data-urlencode "v_DEMO=$SENTINEL" "$URL")"
echo "http_code=$CODE"
test "$CODE" = "410"

echo "[10/10] 停止隔离实例"
SEC_HOME="$STATE_DIR" SEC_PORT="$PORT" "$SEC" stop >/dev/null
echo "SMOKE=PASS"