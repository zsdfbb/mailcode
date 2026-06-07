# mailcode doc 命令 + 资源打包修复 执行计划

> 每个任务按目录划分，同一目录下的所有变更由一个 agent 完成。按依赖顺序执行。

## 上下文引用

参考设计计划：`docs/design-plans/2026-06-02-mailcode-doc.md`

## 任务清单

### Task 1: 8 份 Markdown 文档新增
- **涉及目录**: `mailcode/resources/docs/`
- **涉及文件**:
  - `mailcode/resources/docs/overview.md`
  - `mailcode/resources/docs/config.md`
  - `mailcode/resources/docs/session.md`
  - `mailcode/resources/docs/scheduler.md`
  - `mailcode/resources/docs/project.md`
  - `mailcode/resources/docs/plugin.md`
  - `mailcode/resources/docs/setup.md`
  - `mailcode/resources/docs/security.md`
- **描述**: 创建 8 份中文 Markdown 文档，每份 30-100 行。`overview.md` 包含完整工作流（装→配→部署→启动→发邮件→收通知）+ 末尾 7 个 topic 索引；其余 7 份分别对应 `mailcode doc <topic>` 输出的细分内容，含典型命令、配置示例、注意事项。
- **验证标准**:
  - [ ] ✅ 8 份文件全部存在
  - [ ] ✅ 每份 UTF-8 编码、以 `#` 标题开头
  - [ ] ✅ 无空文件（每份 ≥ 30 行）
  - [ ] ✅ `overview.md` 末尾包含 7 个 topic 名称作为索引（config / session / scheduler / project / plugin / setup / security）
  - [ ] ✅ 命令示例用 fenced code block 包裹（` ```bash ... ``` `）

### Task 2: pyproject.toml 加 package-data
- **涉及目录**: 项目根
- **涉及文件**: `pyproject.toml`
- **描述**: 在文件末尾追加：
  ```toml
  [tool.setuptools.package-data]
  mailcode = ["resources/*.json", "resources/*.js", "resources/docs/*.md", "templates/email_templates/*.txt"]
  ```
- **验证标准**:
  - [ ] ✅ `python3 -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"` 不抛错
  - [ ] ✅ `grep -A3 'package-data' pyproject.toml` 输出含 `resources/docs/*.md`
  - [ ] ✅ `bash build.sh` 后 `unzip -l dist/mailcode-*.whl | grep -c resources/docs` ≥ 8

### Task 3: cli.py 新增 cmd_doc + 加强 cmd_setup
- **涉及目录**: `mailcode/`
- **涉及文件**: `mailcode/cli.py`
- **描述**:
  1. 新增 `cmd_doc(args)` 函数：用 `get_resource_path("docs/{topic or 'overview'}.md").read_text(encoding="utf-8")` 读；读不到时按 topic 是否给定分别报错（无 topic：总览缺失；有 topic：列出 7 个可用 topic）。
  2. 新增 `p_doc` 子解析器：`topic` 用 `nargs="?"` 默认 None，`choices=["config", "session", "scheduler", "project", "plugin", "setup", "security"]`，`help="查看 mailcode 工作流/使用指南"`.
  3. `main()` 中注册 `elif args.command == "doc": cmd_doc(args)`.
  4. 修改 `cmd_setup` 中 `mailcode-bridge.js` / `claude-code-hooks.json` 缺失时的提示，改为多行 stderr 输出，明确指引 wheel 用户重跑 `bash install.sh`。
- **验证标准**:
  - [ ] ✅ `python3 -c "from mailcode.cli import cmd_doc"` 不抛错
  - [ ] ✅ `mailcode --help` 输出含 `doc`
  - [ ] ✅ `mailcode doc` 打印 overview 内容
  - [ ] ✅ `mailcode doc config` 打印 config.md
  - [ ] ✅ `mailcode doc nope` argparse 拒绝（SystemExit 码 2）
  - [ ] ✅ `mailcode setup` 在资源缺失时打印新的多行提示

### Task 4: tests/unit/test_cli_doc.py 新增 6 个用例
- **涉及目录**: `tests/unit/`
- **涉及文件**: `tests/unit/test_cli_doc.py`
- **描述**: 按设计计划"测试策略"中列出的 6 个用例实现：
  1. `test_overview_prints_index` — 调 `cmd_doc(args)` 无 topic，验证输出含 7 个 topic 关键词
  2. `test_known_topic`（参数化 7 个） — 验证每个 topic 输出对应内容
  3. `test_unknown_topic_rejected` — `parser.parse_args(["doc", "nope"])` 抛 SystemExit
  4. `test_parser_default_topic_none`
  5. `test_parser_known_topic`
  6. `test_pyproject_package_data_present` — 解析 pyproject.toml 验证 package-data 含 `resources/docs/*.md`
- **验证标准**:
  - [ ] ✅ `pytest tests/unit/test_cli_doc.py -q` 全部通过
  - [ ] ✅ `pytest tests/unit/ -q` 全套无回归

### Task 5: install.sh 改 build + wheel 流程
- **涉及目录**: 项目根
- **涉及文件**: `install.sh`
- **描述**: 替换第 108-134 行"── 2. 安装 mailcode 包 ──"段：
  - `--local <wheel>` 行为不变（跳过 build 直接装）
  - 无 `--local` 时：
    1. `info "构建 wheel（首次约 30-60 秒）"`
    2. `bash "${MAILCODE_DIR}/build.sh"`
    3. `BUILT_WHEEL=$(ls -t dist/mailcode-*.whl | head -1)`
    4. `${PIP_CMD} install ${PIP_FLAGS} --force-reinstall "${BUILT_WHEEL}"`
  - build.sh 失败立即 `exit 1`，不进入 pip install
- **验证标准**:
  - [ ] ✅ `bash -n install.sh` 语法检查通过
  - [ ] ✅ `grep -n "build.sh" install.sh` 命中
  - [ ] ✅ `grep -n "force-reinstall" install.sh` 命中
  - [ ] ✅ `bash install.sh --help` 输出与之前一致
  - [ ] ✅ 手动跑 `rm -rf dist && bash install.sh` 能完成 build + install

### Task 6: 文档更新（CLAUDE.md + README + design.md）
- **涉及目录**: 项目根 + `docs/design-final/`
- **涉及文件**: `CLAUDE.md`, `README.md`, `README.en.md`, `docs/design-final/design.md`
- **描述**:
  - `CLAUDE.md` "常用命令" 段追加：`- **查看使用指南**: mailcode doc [topic]`
  - `README.md` "统一 CLI" 表格后追加：> 详细工作流指南：mailcode doc（中文）
  - `README.en.md` 同样位置加英文：> Workflow guide: mailcode doc
  - `docs/design-final/design.md` 末尾追加"5. CLI 自描述"章节，说明 `mailcode doc` 在工作流中的位置
- **验证标准**:
  - [ ] ✅ `grep -n "mailcode doc" CLAUDE.md README.md README.en.md` 各命中至少 1 次
  - [ ] ✅ `docs/design-final/design.md` 含"5. CLI 自描述"章节

### Task 7: 整体验证
- **涉及目录**: 项目根
- **涉及文件**: （无新增/修改，仅验证）
- **描述**: 跑全量检查：
  1. `source .venv/bin/activate && python3 -m pytest tests/unit/ -q`
  2. `source .venv/bin/activate && python3 -m ruff check mailcode/ tests/`
  3. `rm -rf dist build && python3 -m build --wheel && unzip -l dist/mailcode-*.whl | grep -E 'resources/(.*\.md|.*\.json|.*\.js)' | wc -l` ≥ 11
  4. `bash install.sh` 端到端：build + install
  5. fresh venv：`pip install dist/mailcode-*.whl` 后 `mailcode doc` 可用
- **验证标准**:
  - [ ] ✅ 全部 pytest 通过
  - [ ] ✅ ruff 0 问题
  - [ ] ✅ wheel 中 resources/*.md|json|js 数 ≥ 11（3 原有 + 8 新 .md）
  - [ ] ✅ `bash install.sh` 端到端完成
  - [ ] ✅ fresh venv 里 `mailcode doc` 输出 overview

## 执行顺序

```
Task 1 (docs/)   ──┐
Task 2 (pyproject)──┤ 互不冲突，可并发
Task 3 (cli.py)   ──┘
       │
       ├──► Task 4 (test_cli_doc.py) — 依赖 Task 3
       │
       Task 5 (install.sh) — 依赖 Task 2（pyproject 配好后 build 才有正确 wheel）
       │
       Task 6 (CLAUDE.md / README / design.md) — 与 Task 5 可并发，无文件冲突
       │
       └──► Task 7 (整体验证) — 最后
```

## 验证清单

- [ ] `source .venv/bin/activate && python3 -m pytest tests/unit/ -q` — 全部通过（含新增 test_cli_doc）
- [ ] `source .venv/bin/activate && python3 -m ruff check mailcode/ tests/` — 无问题
- [ ] `bash build.sh` — 成功，产物在 `dist/`
- [ ] `unzip -l dist/mailcode-*.whl | grep resources/` — 含 3 个原有 + 8 个新 .md = 11 项
- [ ] `bash -n install.sh` — 语法 OK
- [ ] fresh venv 端到端：
  - [ ] `python3 -m venv /tmp/mailcode-test-venv`
  - [ ] `/tmp/mailcode-test-venv/bin/pip install dist/mailcode-*.whl`
  - [ ] `/tmp/mailcode-test-venv/bin/mailcode doc` — 输出 overview
  - [ ] `/tmp/mailcode-test-venv/bin/mailcode doc config` — 输出 config.md 内容
