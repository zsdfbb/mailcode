# MailCode

Python 邮件连接器，通过邮件远程操控 AI 助手（OpenCode / Claude Code）。

## 技术栈
- Python 3.x · IMAP/SMTP · Claude Code
- 零第三方依赖（全标准库）

## 目录结构
```
MailCode/
├── mailcode/          # 包目录
│   ├── cli.py         # 统一 CLI 入口
│   ├── config.py      # 配置加载
│   ├── health.py      # 连通性检查
│   ├── provider_presets.py  # 邮件服务商预设
│   ├── server.py      # 监听服务入口
│   ├── session_cli.py # session 子命令的 CLI 呈现
│   ├── relay/         # IMAP 监听/安全/Session 管理
│   │   ├── conversation_handler.py  # Session 管理（per-file + index + cwd）
│   │   ├── email_listener.py        # IMAP 监听
│   │   └── security.py              # 安全模块
│   ├── channels/      # 邮件发送通道
│   │   └── email_channel.py
│   ├── resources/     # 资源文件（默认配置）
│   │   └── default.json
│   └── utils/         # 工具模块
│       └── logging.py
├── tests/             # 测试
│   ├── unit/          # 单元测试
│   ├── integration/   # 集成测试
│   ├── binary/        # 二进制/端到端测试
│   └── run_tests.sh   # 测试运行脚本
├── docs/              # 设计文档
├── build.sh           # 构建脚本（python -m build 构建 wheel 到 dist/）
├── install.sh         # 一键安装脚本
├── prepare.sh         # 开发环境准备
├── release.sh         # 发布脚本
├── requirements-dev.txt # 开发依赖
├── uninstall.sh       # 卸载脚本
└── pyproject.toml     # 包配置
```

## 常用命令
- **开发安装**: `bash install.sh`
- **构建 wheel**: `bash build.sh`
- **启动中继**: `mailcode serve --idle`（或 `--once` 单次轮询、`--dry-run` 干跑）
- **配置**: `mailcode config init` / `mailcode config show` / `mailcode config validate`
- **集成测试配置**: `mailcode config init-test`
- **项目管理**: `mailcode project`（管理项目目录）
- **检查连通性**: `mailcode health`
- **会话管理**: `mailcode session <list|show|delete|cleanup>`（session 列表/详情/删除/按 TTL 清理；`cleanup --dry-run` 仅预览）
- **激活虚拟环境**: `source .venv/bin/activate`
- **测试**: `source .venv/bin/activate && python3 -m pytest tests/unit/ -q`
- **代码检查**: `source .venv/bin/activate && python3 -m ruff check mailcode/ tests/`

## 工作准则
- 代码注释和文档用中文，面向社区的文档（README）用英文
- 通过 `.venv` 虚拟环境运行测试和 lint：`source .venv/bin/activate && python3 -m pytest tests/unit/ -q && python3 -m ruff check mailcode/ tests/`
- 配置通过 `~/.config/mailcode/config.json` 管理；集成测试配置通过 `~/.config/mailcode/test_config.json`
- 日志级别可通过 `MAILCODE_LOG_LEVEL` 环境变量调整（默认 INFO）
- 一般开发/测试命令（pytest、ruff、`bash -n`、参数解析等）不显式设 `timeout`，用默认 120s 就够；只有 `pip install` / `python -m build` / 端到端集成测试这类长任务才需要调高
