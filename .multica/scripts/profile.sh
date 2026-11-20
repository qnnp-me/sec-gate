#!/bin/sh
# .multica/scripts/profile.sh — 生成这个仓库的项目画像事实段（.multica/PROJECT.md）。
#
#   sh .multica/scripts/profile.sh                只读：把完整 PROJECT.md 打到 stdout
#   sh .multica/scripts/profile.sh --write        只替换事实段，人写段（Owner/目录职责/怎么跑起来）原样保留
#   sh .multica/scripts/profile.sh --fingerprint  只打印指纹键值，供 .multica/scripts/check-profile.sh 比对
#   sh .multica/scripts/profile.sh --help
#
# 约束：
#   - POSIX sh，零运行期依赖：只用 git 和 POSIX 工具，不联网，不执行仓库里的构建/测试命令。
#   - 只读：除 --write 外不写任何文件；--write 也只动 PROJECT.md，且只动事实段。
#   - 可复现：事实段只由工作树内容决定（last_verified_at 取当天 UTC 日期），
#     同一工作树同一天跑两次，输出逐字节相同。
#   - 不落任何密钥、env 值、本机绝对路径；只记录仓库里的事实。

set -eu

FACTS_BEGIN='<!-- profile:facts:begin -->'
FACTS_END='<!-- profile:facts:end -->'
PROJECT_REL='.multica/PROJECT.md'

usage() {
  awk 'NR>1 { if (/^set -eu/) exit; if (/^#/) { sub(/^# ?/, ""); print } }' "$0"
}

# ---------- 仓库定位 ----------

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(git -C "$SELF_DIR" rev-parse --show-toplevel 2>/dev/null || true)
[ -n "$ROOT" ] || ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[ -n "$ROOT" ] || {
  echo "profile: 不在 git 工作树里，无法探测（本脚本依赖 git 读 HEAD/分支/文件哈希）。" >&2
  exit 1
}
cd "$ROOT" || exit 1

PROJECT_ABS="$ROOT/$PROJECT_REL"

# ---------- 哈希 ----------
# 统一用 git 的 blob 哈希：不引入 sha256sum/openssl 之类的额外依赖，
# 且对同样的字节永远给出同样的值。展示用前 12 位。

hash_stdin() {
  git hash-object --no-filters --stdin
}

hash_file() {
  git hash-object --no-filters -- "$1"
}

short12() {
  cut -c1-12
}

# ---------- 探测 ----------

detect_branch() {
  b=$(git symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null || true)
  b=${b#origin/}
  if [ -z "$b" ]; then
    # clone/CI 里 origin/HEAD 常常没设；按常见默认分支名再试一次。
    for cand in main master trunk develop; do
      if git show-ref -q --verify "refs/heads/$cand" ||
        git show-ref -q --verify "refs/remotes/origin/$cand"; then
        b=$cand
        break
      fi
    done
  fi
  if [ -z "$b" ]; then
    b=$(git rev-parse --abbrev-ref HEAD)
    case "$b" in
      HEAD) b='（detached HEAD，未判定）' ;;
    esac
  fi
  printf '%s' "$b"
}

# 锁文件与依赖清单：只收仓库里真实存在的，排序保证顺序稳定。
deps_lines() {
  {
    for f in pyproject.toml setup.py setup.cfg requirements.txt \
      poetry.lock uv.lock Pipfile.lock pdm.lock \
      package.json package-lock.json pnpm-lock.yaml yarn.lock bun.lockb \
      go.mod go.sum Cargo.toml Cargo.lock \
      Gemfile Gemfile.lock composer.json composer.lock pom.xml; do
      [ -f "$f" ] || continue
      printf '%s=%s\n' "$f" "$(hash_file "$f" | short12)"
    done
    for f in requirements*.txt; do
      [ -f "$f" ] || continue
      [ "$f" = 'requirements.txt' ] && continue
      printf '%s=%s\n' "$f" "$(hash_file "$f" | short12)"
    done
  } | sort
}

# CI 定义：存在性 + 内容哈希（内容变了说明门禁可能变了，要重新验证画像）。
ci_lines() {
  git ls-files |
    grep -E '^(\.github/workflows/[^/]+\.ya?ml|\.gitlab-ci\.yml|\.circleci/config\.yml|Jenkinsfile|azure-pipelines\.yml|\.travis\.yml|\.drone\.yml)$' |
    sort |
    while IFS= read -r f; do
      [ -f "$f" ] || continue
      printf '%s=%s\n' "$f" "$(hash_file "$f" | short12)"
    done
}

# 顶层目录（只取目录，git 里已排序）。
top_dirs_list() {
  git ls-tree --name-only -d HEAD | sort | tr '\n' ' ' | sed 's/ *$//'
}

# Python src 布局：测试需要 PYTHONPATH=src 才能导入包。
py_srcprefix() {
  if [ -f pyproject.toml ] &&
    grep -qE '(where *= *\[ *"src" *\]|"" *= *"src")' pyproject.toml 2>/dev/null; then
    printf 'PYTHONPATH=src '
  fi
}

# 测试目录里 test_*.py 的数量（不含 __init__.py）。
count_test_files() {
  n=0
  for f in tests/test_*.py; do
    [ -f "$f" ] || continue
    n=$((n + 1))
  done
  printf '%s' "$n"
}

# 探测构建 / 测试 / lint / 启动。只读仓库元数据，不执行命令。
# 结果放进全局：CMD_BUILD/BUILD_SRC、CMD_TEST/TEST_SRC、CMD_LINT/LINT_SRC、CMD_START/START_SRC。
#
# 注意：非 Python 的分支（npm / Makefile / go / cargo / golangci）是给老项目留的降级路径，
# 尚未在第二个仓库上验证过；试点通过、接入下一个老项目时再校准，别把它们当成已验证的探测。
detect_commands() {
  CMD_BUILD=''
  BUILD_SRC=''
  CMD_TEST=''
  TEST_SRC=''
  CMD_LINT=''
  LINT_SRC=''
  CMD_START=''
  START_SRC=''

  # --- 构建 ---
  if [ -f pyproject.toml ] && grep -q '^\[build-system\]' pyproject.toml 2>/dev/null; then
    CMD_BUILD='python3 -m pip install .'
    BUILD_SRC='pyproject.toml [build-system]'
  elif [ -f package.json ] && grep -q '"build"' package.json 2>/dev/null; then
    CMD_BUILD='npm ci && npm run build'
    BUILD_SRC='package.json scripts.build'
  elif [ -f package.json ]; then
    CMD_BUILD='npm ci'
    BUILD_SRC='package.json（无 build 脚本，只装依赖）'
  elif [ -f Makefile ] && grep -qE '^build:' Makefile 2>/dev/null; then
    CMD_BUILD='make build'
    BUILD_SRC='Makefile build 目标'
  elif [ -f go.mod ]; then
    CMD_BUILD='go build ./...'
    BUILD_SRC='go.mod'
  elif [ -f Cargo.toml ]; then
    CMD_BUILD='cargo build'
    BUILD_SRC='Cargo.toml'
  fi

  # --- 测试 ---
  prefix=$(py_srcprefix)
  if { [ -f pyproject.toml ] && grep -q 'pytest' pyproject.toml 2>/dev/null; } ||
    { [ -f setup.cfg ] && grep -q '\[tool:pytest\]' setup.cfg 2>/dev/null; } ||
    [ -f pytest.ini ] || { [ -f tox.ini ] && grep -q 'pytest' tox.ini 2>/dev/null; }; then
    CMD_TEST="${prefix}python3 -m pytest"
    TEST_SRC='仓库声明了 pytest 配置'
  elif [ -d tests ] && [ "$(count_test_files)" -gt 0 ]; then
    CMD_TEST="${prefix}python3 -m unittest discover -s tests -v"
    TEST_SRC="tests/，$(count_test_files) 个 test_*.py"
  elif [ -f package.json ] && grep -q '"test"' package.json 2>/dev/null; then
    CMD_TEST='npm test'
    TEST_SRC='package.json scripts.test'
  elif [ -f Makefile ] && grep -qE '^test:' Makefile 2>/dev/null; then
    CMD_TEST='make test'
    TEST_SRC='Makefile test 目标'
  elif [ -f go.mod ]; then
    CMD_TEST='go test ./...'
    TEST_SRC='go.mod'
  elif [ -f Cargo.toml ]; then
    CMD_TEST='cargo test'
    TEST_SRC='Cargo.toml'
  fi

  # --- lint ---
  if [ -f pyproject.toml ] && grep -q '^\[tool\.ruff' pyproject.toml 2>/dev/null; then
    CMD_LINT='python3 -m ruff check .'
    LINT_SRC='pyproject.toml [tool.ruff]'
  elif { [ -f .flake8 ] || { [ -f setup.cfg ] && grep -q '\[flake8\]' setup.cfg 2>/dev/null; } ||
    { [ -f tox.ini ] && grep -q '\[flake8\]' tox.ini 2>/dev/null; }; }; then
    CMD_LINT='python3 -m flake8'
    LINT_SRC='flake8 配置文件'
  elif [ -f package.json ] && grep -q '"lint"' package.json 2>/dev/null; then
    CMD_LINT='npm run lint'
    LINT_SRC='package.json scripts.lint'
  elif [ -f Makefile ] && grep -qE '^lint:' Makefile 2>/dev/null; then
    CMD_LINT='make lint'
    LINT_SRC='Makefile lint 目标'
  elif [ -f .golangci.yml ] || [ -f .golangci.yaml ]; then
    CMD_LINT='golangci-lint run'
    LINT_SRC='golangci-lint 配置'
  else
    # 没有 lint 配置，但 CI 里跑了 ruff：照 CI 的命令记，并注明要自己装 ruff。
    ci_hit=''
    ci_files=$(ci_lines | cut -d= -f1)
    for f in $ci_files; do
      if grep -q 'ruff check' "$f" 2>/dev/null; then
        ci_hit=$f
        break
      fi
    done
    if [ -n "$ci_hit" ]; then
      ruff_ver=$(grep -ho 'ruff==[0-9][0-9.]*' "$ci_hit" 2>/dev/null | head -1 | sed 's/^ruff==//' || true)
      CMD_LINT='python3 -m ruff check .'
      if [ -n "$ruff_ver" ]; then
        LINT_SRC="CI $ci_hit 的 ruff check，锁定 ruff==$ruff_ver，需先安装"
      else
        LINT_SRC="CI $ci_hit 的 ruff check，需先安装 ruff"
      fi
    fi
  fi

  # --- 启动 ---
  start_parts=''
  start_src=''
  if [ -f pyproject.toml ]; then
    entry_names=$(sed -n '/^\[project\.scripts\]/,/^\[/p' pyproject.toml |
      sed -n 's/^\([A-Za-z0-9_.-]*\) *=.*/\1/p' | sort -u | tr '\n' ' ' | sed 's/ *$//')
    if [ -n "$entry_names" ]; then
      start_parts=$(printf '%s' "$entry_names" | sed 's/ /、/g')
      start_parts="安装后入口：$start_parts"
      start_src='pyproject.toml [project.scripts]'
    fi
  fi
  for d in src/*/; do
    [ -d "$d" ] || continue
    [ -f "${d}__main__.py" ] || continue
    pkg=${d#src/}
    pkg=${pkg%/}
    if [ -n "$start_parts" ]; then
      start_parts="$start_parts；python3 -m $pkg"
    else
      start_parts="python3 -m $pkg"
    fi
    start_src=${start_src:+$start_src、}"src/$pkg/__main__.py"
  done
  if [ -z "$start_parts" ]; then
    if [ -f package.json ] && grep -q '"start"' package.json 2>/dev/null; then
      start_parts='npm start'
      start_src='package.json scripts.start'
    elif [ -f Makefile ] && grep -qE '^(run|serve):' Makefile 2>/dev/null; then
      start_parts='make run'
      start_src='Makefile run/serve 目标'
    fi
  fi
  CMD_START=$start_parts
  START_SRC=$start_src
}

# ---------- 渲染 ----------

render_facts() {
  detect_commands
  HEAD_SHORT=$(git rev-parse --short HEAD)
  BRANCH=$(detect_branch)
  DEPS=$(deps_lines)
  CI=$(ci_lines)
  TOP_DIRS=$(top_dirs_list)
  TOP_DIRS_SHA=$(printf '%s\n' "$TOP_DIRS" | hash_stdin | short12)
  CMD_SHA=$(printf 'build=%s\ntest=%s\n' "$CMD_BUILD" "$CMD_TEST" | hash_stdin | short12)
  NOW=$(date -u +%Y-%m-%d)

  printf '%s\n' "$FACTS_BEGIN"
  printf 'last_verified: %s\n' "$HEAD_SHORT"
  printf 'last_verified_at: %s\n' "$NOW"
  printf '\n'
  printf '## 指纹\n'
  printf '\n'
  printf '%s\n' "- default_branch: $BRANCH"
  printf '%s\n' "- head: $HEAD_SHORT"
  if [ -n "$DEPS" ]; then
    printf '%s\n' "$DEPS" | while IFS= read -r line; do
      printf '%s\n' "- deps: $line"
    done
  else
    printf '%s\n' '- deps: （未探测到锁文件或依赖清单）'
  fi
  if [ -n "$CI" ]; then
    printf '%s\n' "$CI" | while IFS= read -r line; do
      printf '%s\n' "- ci: $line"
    done
  else
    printf '%s\n' '- ci: （未探测到 CI 定义）'
  fi
  if [ -n "$TOP_DIRS" ]; then
    printf '%s\n' "- top_dirs: $TOP_DIRS"
  else
    printf '%s\n' '- top_dirs: （空仓库）'
  fi
  printf '%s\n' "- top_dirs_sha: $TOP_DIRS_SHA"
  printf '%s\n' "- cmd_build_test_sha: $CMD_SHA"

  printf '\n'
  printf '## 命令\n'
  printf '\n'
  if [ -n "$CMD_BUILD" ]; then
    printf '%s\n' "- 构建: $CMD_BUILD — 可用（$BUILD_SRC）"
  else
    printf '%s\n' '- 构建: （未探测到）'
  fi
  if [ -n "$CMD_TEST" ]; then
    printf '%s\n' "- 测试: $CMD_TEST — 可用（$TEST_SRC）"
  else
    printf '%s\n' '- 测试: （未探测到）'
  fi
  if [ -n "$CMD_LINT" ]; then
    printf '%s\n' "- lint: $CMD_LINT — 可用（$LINT_SRC）"
  else
    printf '%s\n' '- lint: （未探测到）'
  fi
  if [ -n "$CMD_START" ]; then
    printf '%s\n' "- 启动: $CMD_START — 可用（$START_SRC）"
  else
    printf '%s\n' '- 启动: （未探测到）'
  fi
  printf '%s\n' "$FACTS_END"
}

render_fingerprint() {
  detect_commands
  HEAD_SHORT=$(git rev-parse --short HEAD)
  BRANCH=$(detect_branch)
  DEPS=$(deps_lines)
  CI=$(ci_lines)
  TOP_DIRS=$(top_dirs_list)
  TOP_DIRS_SHA=$(printf '%s\n' "$TOP_DIRS" | hash_stdin | short12)
  CMD_SHA=$(printf 'build=%s\ntest=%s\n' "$CMD_BUILD" "$CMD_TEST" | hash_stdin | short12)

  printf 'default_branch: %s\n' "$BRANCH"
  printf 'head: %s\n' "$HEAD_SHORT"
  if [ -n "$DEPS" ]; then
    printf '%s\n' "$DEPS" | while IFS= read -r line; do printf 'deps: %s\n' "$line"; done
  else
    printf 'deps: （未探测到锁文件或依赖清单）\n'
  fi
  if [ -n "$CI" ]; then
    printf '%s\n' "$CI" | while IFS= read -r line; do printf 'ci: %s\n' "$line"; done
  else
    printf 'ci: （未探测到 CI 定义）\n'
  fi
  if [ -n "$TOP_DIRS" ]; then
    printf 'top_dirs: %s\n' "$TOP_DIRS"
  else
    printf 'top_dirs: （空仓库）\n'
  fi
  printf 'top_dirs_sha: %s\n' "$TOP_DIRS_SHA"
  printf 'cmd_build_test_sha: %s\n' "$CMD_SHA"
  printf 'cmd_build: %s\n' "${CMD_BUILD:-（未探测到）}"
  printf 'cmd_test: %s\n' "${CMD_TEST:-（未探测到）}"
}

default_human() {
  cat <<'EOF'
## Owner

[LEAD]技术负责人

## 目录职责

- `src/sec_gate/`：唯一的运行期代码包——`cli.py` 命令行入口、`httpapi.py` 与 `service.py` 本地服务、`mcp.py` MCP 适配、`store.py` 一次性请求状态、`risk.py` 命令风险分级、`setup.py` 注册 MCP 条目与 skill。
- `tests/`：unittest 单测，覆盖风险分级、runner、store、setup 与隐私守卫。
- `scripts/`：CI 复用的守卫脚本——`privacy_scan.sh` 隐私扫描、`smoke.sh` 端到端冒烟。
- `docs/`：设计与取舍记录（`DESIGN.md`）。
- `.github/workflows/`：CI 定义（lint、隐私扫描、单测、安装与入口点、冒烟）。

## 怎么跑起来

运行期只依赖 Python 3.11+ 标准库，没有第三方依赖。

```sh
python3 -m venv .venv
.venv/bin/pip install .                                  # 得到 sec / sec-mcp 两个入口
PYTHONPATH=src python3 -m unittest discover -s tests -v  # 单测
```

不安装时直接从源码跑：`PYTHONPATH=src python3 -m sec_gate --help`。

服务侧入口是 `sec serve`；隔离实例用变量 `SEC_HOME`（状态目录）与 `SEC_PORT`（端口）指定，
CI 里的 `scripts/smoke.sh` 就是这样起一个隔离实例做冒烟的。
EOF
}

preamble() {
  cat <<'EOF'
# sec-gate 项目画像

本文件只记录**这个仓库的事实**，供研发小组在动手前一次读全，省掉每个 run 重读仓库。

- 事实段（文件头、指纹、命令）由 `.multica/scripts/profile.sh` 生成，不要手改；
  重建：`sh .multica/scripts/profile.sh --write`。
- Owner 与「目录职责」「怎么跑起来」是人写段，`--write` 不会覆盖。
- 团队规则（分级、安全触发词、收口格式）不在本文件里，留在 workspace 一处，避免 N 个仓库 N 份副本漂移。

EOF
}

# 取出 PROJECT.md 里标记之外的内容：
#   before_facts <file>  事实段之前的部分（含标题与人写前言）
#   after_facts  <file>  事实段之后的部分（人写段原样）
before_facts() {
  awk -v b="$FACTS_BEGIN" '$0==b{exit} {print}' "$1"
}

after_facts() {
  awk -v e="$FACTS_END" 'found{print} $0==e{found=1}' "$1"
}

has_markers() {
  grep -qF -- "$FACTS_BEGIN" "$1" 2>/dev/null && grep -qF -- "$FACTS_END" "$1" 2>/dev/null
}

emit_full() {
  if [ -f "$PROJECT_ABS" ] && has_markers "$PROJECT_ABS"; then
    before_facts "$PROJECT_ABS"
    render_facts
    after_facts "$PROJECT_ABS"
  else
    preamble
    render_facts
    printf '\n'
    default_human
  fi
}

write_profile() {
  if [ -f "$PROJECT_ABS" ] && ! has_markers "$PROJECT_ABS"; then
    echo "profile: $PROJECT_REL 里找不到事实段标记（$FACTS_BEGIN / $FACTS_END），未改动任何文件。" >&2
    echo "profile: 请手工恢复标记，或删除该文件后重跑 --write。" >&2
    exit 1
  fi
  mkdir -p "$ROOT/.multica/scripts"
  tmp=$(mktemp "${TMPDIR:-/tmp}/project.XXXXXX")
  emit_full >"$tmp"
  mv "$tmp" "$PROJECT_ABS"
  echo "profile: 已写入 $PROJECT_REL（last_verified: $(git rev-parse --short HEAD)）"
}

case "${1:-}" in
  '')
    emit_full
    ;;
  --write)
    write_profile
    ;;
  --fingerprint)
    render_fingerprint
    ;;
  --help | -h)
    usage
    ;;
  *)
    echo "profile: 未知参数 '$1'（可用：--write / --fingerprint / --help）" >&2
    exit 2
    ;;
esac