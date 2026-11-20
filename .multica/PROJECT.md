# sec-gate 项目画像

本文件只记录**这个仓库的事实**，供研发小组在动手前一次读全，省掉每个 run 重读仓库。

- 事实段（文件头、指纹、命令）由 `.multica/scripts/profile.sh` 生成，不要手改；
  重建：`sh .multica/scripts/profile.sh --write`。
- Owner 与「目录职责」「怎么跑起来」是人写段，`--write` 不会覆盖。
- 团队规则（分级、安全触发词、收口格式）不在本文件里，留在 workspace 一处，避免 N 个仓库 N 份副本漂移。

<!-- profile:facts:begin -->
last_verified: a8c13b1
last_verified_at: 2026-11-20

## 指纹

- default_branch: main
- head: a8c13b1
- deps: pyproject.toml=f65a04110ab3
- ci: .github/workflows/ci.yml=72c274f7b2fa
- top_dirs: .github docs scripts src tests
- top_dirs_sha: 01bcccf043a7
- cmd_build_test_sha: 438cae45691d

## 命令

- 构建: python3 -m pip install . — 可用（pyproject.toml [build-system]）
- 测试: PYTHONPATH=src python3 -m unittest discover -s tests -v — 可用（tests/，5 个 test_*.py）
- lint: python3 -m ruff check . — 可用（CI .github/workflows/ci.yml 的 ruff check，锁定 ruff==0.17.5，需先安装）
- 启动: 安装后入口：sec、sec-mcp；python3 -m sec_gate — 可用（pyproject.toml [project.scripts]、src/sec_gate/__main__.py）
<!-- profile:facts:end -->

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
