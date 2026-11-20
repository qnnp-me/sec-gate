#!/bin/sh
# .multica/scripts/check-profile.sh — 门禁：比对 .multica/PROJECT.md 的指纹与当前工作树。
#
#   sh .multica/scripts/check-profile.sh
#     一致   → exit 0，打印 profile OK (<sha>)
#     不一致 → exit 1，逐条打印哪一项、期望什么、实际什么，并给一行修复提示
#
# 供 CI 与 LEAD 门禁共用：只读、不联网、不执行仓库里的构建/测试。
# 指纹的算法只在 profile.sh 一处，本脚本调用它的 --fingerprint 取“实际值”，
# 从 PROJECT.md 取“期望值”，所以两边定义不会漂移。
#
# head 项的判定：PROJECT.md 记的是“被探测的那个提交”。所以
#   - HEAD 等于它 → 画像新鲜；
#   - 否则，若 last_verified..HEAD 之间只改了画像自身（.multica/ 与 AGENTS.md），
#     视为仍新鲜（否则提交画像本身就会把门禁自己拦住）；
#   - 只要还有别的路径变了（代码/测试/文档/依赖），就报过期，提示重跑 --write。

set -eu

PROJECT_REL='.multica/PROJECT.md'
PROFILER_REL='.multica/scripts/profile.sh'
FACTS_BEGIN='<!-- profile:facts:begin -->'
FACTS_END='<!-- profile:facts:end -->'

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(git -C "$SELF_DIR" rev-parse --show-toplevel 2>/dev/null || true)
[ -n "$ROOT" ] || ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[ -n "$ROOT" ] || {
  echo "check-profile: 不在 git 工作树里。" >&2
  exit 1
}
cd "$ROOT" || exit 1

PROJECT="$ROOT/$PROJECT_REL"
PROFILER="$ROOT/$PROFILER_REL"

missing() {
  echo "profile MISSING: $1" >&2
  echo "run $PROFILER_REL --write and commit"
  exit 1
}

[ -f "$PROJECT" ] || missing "$PROJECT_REL 不存在"
[ -f "$PROFILER" ] || missing "$PROFILER_REL 不存在"

grep -qF -- "$FACTS_BEGIN" "$PROJECT" || missing "$PROJECT_REL 缺少事实段标记 $FACTS_BEGIN"
grep -qF -- "$FACTS_END" "$PROJECT" || missing "$PROJECT_REL 缺少事实段标记 $FACTS_END"

EXPECTED=$(mktemp "${TMPDIR:-/tmp}/profile-expected.XXXXXX")
ACTUAL=$(mktemp "${TMPDIR:-/tmp}/profile-actual.XXXXXX")
FACTS=$(mktemp "${TMPDIR:-/tmp}/profile-facts.XXXXXX")
cleanup() {
  rm -f "$EXPECTED" "$ACTUAL" "$FACTS"
}
trap cleanup EXIT INT TERM

# 期望值：先取出事实段 ``- key: value`` 的子弹行，命令段的两条另换算成 cmd_build/cmd_test。
awk -v b="$FACTS_BEGIN" -v e="$FACTS_END" '
  $0==b {infacts=1; next}
  $0==e {infacts=0}
  infacts {print}
' "$PROJECT" >"$FACTS"

{
  sed -n 's/^- \([a-z_]*\): /\1: /p' "$FACTS"
  grep -E '^(last_verified|last_verified_at): ' "$FACTS" || true
  sed -n 's/^- 构建: \(.*\) — 可用.*$/cmd_build: \1/p; s/^- 构建: \(（未探测到）\)$/cmd_build: \1/p' "$FACTS"
  sed -n 's/^- 测试: \(.*\) — 可用.*$/cmd_test: \1/p; s/^- 测试: \(（未探测到）\)$/cmd_test: \1/p' "$FACTS"
} >"$EXPECTED"

sh "$PROFILER" --fingerprint >"$ACTUAL"

# 取某个 key 的全部值（deps / ci 可能多行），用换行分隔。
vals() {
  sed -n "s/^$2: //p" "$1"
}

# 列表型指纹（deps / ci）逐条比，输出形如 ``deps[pyproject.toml]: 期望 x，实际 y``。
list_diff() {
  label=$1
  names=$(
    {
      vals "$EXPECTED" "$label" | sed -n 's/^\([^=]*\)=.*/\1/p'
      vals "$ACTUAL" "$label" | sed -n 's/^\([^=]*\)=.*/\1/p'
    } | sort -u
  )
  for n in $names; do
    e=$(vals "$EXPECTED" "$label" | grep -F "$n=" | head -1 | cut -d= -f2-)
    a=$(vals "$ACTUAL" "$label" | grep -F "$n=" | head -1 | cut -d= -f2-)
    if [ "$e" = "$a" ]; then
      continue
    fi
    report "$label[$n]" "${e:-（缺失）}" "${a:-（缺失）}"
  done
}

# 提示里的“期望/实际”统一走这里，计数也在这里。
COUNT=0
report() {
  COUNT=$((COUNT + 1))
  printf '  - %s: 期望 %s，实际 %s\n' "$1" "$2" "$3"
}

# 标量指纹：逐项比。
scalar_diff() {
  key=$1
  e=$(vals "$EXPECTED" "$key")
  a=$(vals "$ACTUAL" "$key")
  [ "$e" = "$a" ] || report "$key" "${e:-（缺失）}" "${a:-（缺失）}"
}

# head：见文件头说明——只有“画像自身文件之外”的变化才算过期。
HEAD_SHORT=$(git rev-parse --short HEAD)
check_head() {
  e=$(vals "$EXPECTED" 'head' | head -1)
  [ "$e" = "$HEAD_SHORT" ] && return 0
  if [ -n "$e" ] && git rev-parse -q --verify "$e^{commit}" >/dev/null 2>&1; then
    changed=$(git diff --name-only "$e" HEAD 2>/dev/null || true)
    [ -z "$changed" ] && return 0
    unrelated=$(printf '%s\n' "$changed" | grep -vE '^(\.multica/|AGENTS\.md$)' | grep -c . || true)
    if [ "$unrelated" -eq 0 ]; then
      return 0
    fi
  fi
  report 'head' "${e:-（缺失）}" "$HEAD_SHORT"
}

list_diff deps
list_diff ci
# default_branch 只记在画像里，不进门禁：clone 方式不同（PR 构建是 detached HEAD、
# origin/HEAD 未设）会让分支名变化，但那不改变仓库事实，不该拦住提交。
check_head
scalar_diff top_dirs
scalar_diff top_dirs_sha
scalar_diff cmd_build_test_sha
scalar_diff cmd_build
scalar_diff cmd_test

# last_verified 必须等于 head（否则文件自相矛盾：头部标记的验证点不是它自己记的 HEAD）。
lv=$(sed -n 's/^last_verified: //p' "$EXPECTED" | head -1)
hd=$(vals "$EXPECTED" 'head' | head -1)
if [ "$lv" != "$hd" ]; then
  report 'last_verified' "${hd:-（缺失）}（应与 head 一致）" "${lv:-（缺失）}"
fi

if [ "$COUNT" -eq 0 ]; then
  echo "profile OK ($HEAD_SHORT)"
  exit 0
fi

echo "profile MISMATCH ($COUNT 项，HEAD $HEAD_SHORT)"
echo "run $PROFILER_REL --write and commit"
exit 1