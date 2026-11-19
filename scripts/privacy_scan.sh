#!/usr/bin/env bash
# 隐私守卫：仓库里不得出现真实主机名、域名、路径、邮箱、用户名等标识。
#
# 用法：./scripts/privacy_scan.sh [目录]   默认扫描当前目录
#
# 规则：仓库内任何被扫描文件都不得包含模式里的连续字面量；需要举例时
# 在运行时拼接（例如 "mu" + "137"），不要落整段字符串到文件里。
# 本脚本自身含整段模式，靠 --exclude=privacy_scan.sh 排除自己。
set -uo pipefail

PAT='mu137|172\.19\.|dpanel|/home/linuxbrew|hdl\.qnnp|@pfan\.ltd|\bwjp\b'
TARGET="${1:-.}"

if grep -rInE --exclude-dir=.git --exclude=.git --exclude-dir=.venv --exclude-dir=.venv-ruff \
  --exclude-dir=build --exclude-dir=dist --exclude-dir='*.egg-info' --exclude-dir=.ruff_cache \
  --exclude=privacy_scan.sh "$PAT" "$TARGET"; then
  echo "SCAN=FAIL"
  exit 1
fi
echo "SCAN=PASS"
exit 0