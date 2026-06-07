# mailcode doc 命令 + 资源打包修复 设计计划

## 背景

MailCode 的 CLI 自描述能力存在以下三个问题：

1. **缺失 `doc` 子命令**。当前 `mailcode --help` 偏干，只能看到子命令列表与参数；`README.md` 偏英文（`README.en.md`），中文用户读起来绕。新用户上手需跳到外部 wiki 或源码，路径长。
2. **wheel 中资源缺失**。解包 `dist/mailcode-0.3.0-py3-none-any.whl` 时发现 `mailcode/resources/` 下的 `default.json` / `claude-code-hooks.json` / `mailcode-bridge.js` 没被打进 wheel。根因：`pyproject.toml` 只有 `[tool.setuptools.packages.find] include = ["mailcode*"]`，缺 `package-data`，setuptools 默认只收 `.py`。后果：`cmd_setup` 部署 bridge 插件在 pip 装 wheel 后静默丢失；`email_listener.py` 加载邮件模板同样丢失。
3. **开发/产线不一致**。`install.sh` 当前走 `pip install -e .`（editable 模式），把 `mailcode/resources/` 当成普通源码目录直接读到；产线用户用 wheel 安装则缺资源。两套行为不一致，开发期难以发现产线问题。

## 设计

三件事并行解决：

### (a) 新增 `mailcode doc [topic]` 子命令

- 在 `mailcode/cli.py` 的 argparse 中新增子命令 `doc`，接受一个可选位置参数 `topic`。
- 资源目录：`mailcode/resources/docs/`，存放 8 个 Markdown 文档：
  - `overview.md` — 工作流总览（无 topic 时默认打印，末尾列出可用 topic 索引）
  - `config.md` — `mailcode config <动作>` 配置管理
  - `session.md` — `mailcode session <动作>` 会话管理
  - `scheduler.md` — `mailcode scheduler <动作>` 定时任务
  - `project.md` — `mailcode project <动作>` 项目目录
  - `plugin.md` — `mailcode plugin <动作>` 插件管理
  - `setup.md` — `mailcode setup [--plugins|--hooks]` 环境部署
  - `security.md` — `security` 配置段：allowed_senders / blocked_commands / coldstart_confirm / auth_policy
- 加载策略：用 `get_resource_path("docs/{topic or 'overview'}.md")` 定位（沿用 `mailcode/resources/__init__.py` 的 `importlib.resources.files()` 机制），找不到时按 topic 是否给定分别报错。
- 打印策略：直接 `print()` 到 stdout，**不调** `less` / `more` / pager。

### (b) 修复 pyproject.toml 资源打包

- 在 `pyproject.toml` 末尾追加：
  ```toml
  [tool.setuptools.package-data]
  mailcode = ["resources/*.json", "resources/*.js", "resources/docs/*.md", "templates/email_templates/*.txt"]
  ```
- 验证：`bash build.sh` 后 `unzip -l dist/mailcode-*.whl | grep resources/` 看到 docs/*.md + 原有 .json/.js。
- 加强 `cmd_setup` 错误提示：bridge.js / hooks.json 缺失时打印多行 stderr，明确指引 wheel 用户重跑 `bash install.sh`。

### (c) 改 install.sh 默认走 build + wheel 流程

- 当前 `install.sh` 第 108-134 行无参时跑 `pip install -e .`。
- 替换为：
  1. 打印耗时预估
  2. `bash build.sh`（产出 `dist/mailcode-*.whl`）
  3. `pip install --force-reinstall dist/mailcode-*.whl`
- 保留 `--local <wheel>` 旁路（跳过 build 步骤直接装指定 wheel）。

## 架构决策

1. **Markdown 文件 vs Python 字符串**：选 Markdown。理由：可被外部文档工具链消费（grep、`glow`、GitHub 渲染）、diff 友好、降低 CLI 源码体积、IDE 渲染。
2. **不用 `less` / `more` 分页**：保持零第三方依赖；终端用户大多已在带滚动条的客户端；CI / pipe 场景下 `mailcode doc | grep xxx` 必须能直接工作。
3. **argparse `choices` 限制 topic**：用 `choices=` 显式列出 7 个合法 topic，未知 topic 直接 argparse 报错（SystemExit 码 2），避免无意义文件查找。`topic` 整体 `nargs="?"` 可选。
4. **`package-data` vs `MANIFEST.in`**：选 `package-data`。理由：与 `pyproject.toml` 单文件配置一致；PEP 621 推荐方式；IDE 跳转识别更友好。
5. **资源路径用 `importlib.resources`**：不写死 `__file__` 相对路径。editable 安装、wheel 安装、源码运行三种模式都正确。
6. **install.sh 失败立即退出**：`set -euo pipefail` 已在，build 失败不会继续 pip install。

## 涉及文件

**新增**（10 个）:
- `mailcode/resources/docs/overview.md`
- `mailcode/resources/docs/config.md`
- `mailcode/resources/docs/session.md`
- `mailcode/resources/docs/scheduler.md`
- `mailcode/resources/docs/project.md`
- `mailcode/resources/docs/plugin.md`
- `mailcode/resources/docs/setup.md`
- `mailcode/resources/docs/security.md`
- `tests/unit/test_cli_doc.py`
- `docs/design-final/design.md` 追加"5. CLI 自描述"章节

**修改**（6 个）:
- `mailcode/cli.py`（新增 `cmd_doc` / `p_doc` / main 分支 / 加强 `cmd_setup` 错误提示）
- `pyproject.toml`（追加 `[tool.setuptools.package-data]` 段）
- `install.sh`（第 108-134 行替换为 build + wheel 流程）
- `CLAUDE.md`（"常用命令" 段加 `mailcode doc` 一行）
- `README.md`（"统一 CLI" 表格后追加引导语）
- `README.en.md`（同步）

合计 16 个文件。

## 测试策略

- pytest 单元测试，集中在 `tests/unit/test_cli_doc.py`：
  1. `test_overview_prints_index` — `mailcode doc` 打印 overview 且包含 7 个 topic 关键词
  2. `test_known_topic`（参数化 7 个 topic） — 打印对应内容，含子命令示例
  3. `test_unknown_topic_rejected` — argparse 拒绝（SystemExit）
  4. `test_parser_default_topic_none` — `parser.parse_args(["doc"]).topic is None`
  5. `test_parser_known_topic` — `parser.parse_args(["doc", "config"]).topic == "config"`
  6. `test_pyproject_package_data_present` — 解析 pyproject.toml 验证 package-data 段含 `resources/docs/*.md`
- 不 TDD（CLI 输出验证不需要驱动开发），6 个 case 一并写好后整体跑。
- 验收标准可自动化：测试通过 + ruff 通过 + wheel 包含 docs/*.md。

## 波及文档

- `docs/design-final/design.md` 需追加"5. CLI 自描述"章节，说明 `mailcode doc` 在工作流中的位置。
- `CLAUDE.md` 的"常用命令"段需补 `mailcode doc` 与 `mailcode doc <topic>` 示例。
- `README.md` / `README.en.md` 在"统一 CLI"段后加一行引导语。

## 风险与注意事项

1. **wheel 必须含 docs/**：若用户从老 wheel 升级，`pip install --force-reinstall` 才会覆盖；老 wheel 中 `mailcode doc` 会因找不到资源报错。
2. **`cmd_doc` 内不要 import 重模块**：保持 `cmd_xxx` 的"懒导入"风格，只在函数内 import `mailcode.resources`。
3. **`install.sh` 变慢需明示**：从 editable 的 ~5 秒到 build + wheel 的 ~30-60 秒，install.sh 内必须用 `info` 步骤明示耗时预估。
4. **`package-data` 通配符范围**：用 `resources/docs/*.md` 而非 `resources/docs/**/*`，避免未来在 docs 下加图片导致 wheel 体积爆掉。
5. **跨平台路径**：`get_resource_path` 返回 `Traversable`，读取用 `read_text(encoding="utf-8")`，不要拼 `os.path`。
