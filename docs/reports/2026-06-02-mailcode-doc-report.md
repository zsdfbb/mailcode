# mailcode doc 命令 + 资源打包修复 修改报告

## 变更摘要
新增 `mailcode doc [topic]` 子命令，提供中文工作流指南（无参数打印总览，带参数打印 config / session / scheduler / project / plugin / setup / security 七大主题）。同时修复 wheel 资源打包 bug（pyproject.toml 缺 package-data 导致 resources/*.json|js 和 templates/email_templates/*.txt 丢失），并改造 install.sh 默认走 build + wheel 流程（与 PyPI 用户拿到一致产物）。

## 文件变更清单
| 操作 | 文件 | 说明 |
|------|------|------|
| A | mailcode/resources/docs/overview.md | 新增 - 工作流总览+主题索引（86 行）|
| A | mailcode/resources/docs/config.md | 新增 - config 子命令详解（85 行）|
| A | mailcode/resources/docs/session.md | 新增 - session 子命令详解（57 行）|
| A | mailcode/resources/docs/scheduler.md | 新增 - scheduler 子命令详解（71 行）|
| A | mailcode/resources/docs/project.md | 新增 - project 子命令详解（62 行）|
| A | mailcode/resources/docs/plugin.md | 新增 - plugin 子命令详解（72 行）|
| A | mailcode/resources/docs/setup.md | 新增 - setup 部署详解（71 行）|
| A | mailcode/resources/docs/security.md | 新增 - security 配置详解（89 行）|
| A | tests/unit/test_cli_doc.py | 新增 - 6 个测试用例（参数化后 18 个）（72 行）|
| M | mailcode/cli.py | 新增 cmd_doc / p_doc / main 分支；加强 cmd_setup 资源缺失提示（587→614 行）|
| M | pyproject.toml | 末尾追加 [tool.setuptools.package-data] 段（17→25 行）|
| M | install.sh | 默认走 bash build.sh + pip install --force-reinstall dist/*.whl（第 108-134 行替换）|
| M | CLAUDE.md | "常用命令"段加 `mailcode doc [topic]` 一行 |
| M | README.md | "统一 CLI"表格后加 `> 详细工作流指南：mailcode doc` 引导语 |
| M | README.en.md | 英文 README 同步加 Workflow guide 引导语 |
| M | docs/design-final/design.md | 末尾追加 "## 11. CLI 自描述" 章节 |

## 测试结果
| 类型 | 命令 | 结果 |
|------|------|------|
| UT | pytest tests/unit/ -q | ✅ 290 passed |
| UT | pytest tests/unit/test_cli_doc.py -v | ✅ 18 passed（含 7 个参数化 topic）|
| Lint | ruff check mailcode/ tests/ | ✅ All checks passed |
| Build | python3 -m build --wheel | ✅ Success，wheel 含 11 项 resources + 4 项 email_templates |
| E2E | bash install.sh（备份配置后） | ✅ 完整跑通：build → install → setup → 链接 |
| E2E | fresh venv + pip install dist/*.whl | ✅ `mailcode doc` / `mailcode doc config` 正常，bridge.js 可读 |

## 关键决策
- 文档用 Markdown 文件（`mailcode/resources/docs/*.md`）而非 Python 字符串——IDE 高亮、diff 友好、易维护
- 资源加载用 `importlib.resources` 的 `get_resource_path()`，避免 `__file__` 相对路径陷阱
- argparse `choices=` 限制 7 个 topic，未知 topic 直接 SystemExit 2
- `install.sh` 默认走 build + wheel 流程（与 PyPI 一致），`--local <wheel>` 旁路保留
- `cmd_setup` 资源缺失时给出明确 wheel 修复指引（`bash install.sh`）
- security.md 中 `auth_policy` 取值按 `default.json` 实际值 `warn/strict/off` 写（不是初稿的 off/token/code）
- design.md 新章节编号为 `## 11. CLI 自描述`（避免与原有 `## 5. 配置设计` 冲突）
