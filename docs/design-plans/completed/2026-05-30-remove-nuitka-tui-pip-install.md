# 移除 Nuitka 与 TUI，支持 pip 安装

## 背景

当前项目仅支持 Nuitka standalone 二进制分发，编译需要 2-8 分钟，效率极低。TUI 功能依赖 `textual` 框架，增加了约 2MB 依赖和 2000+ 行维护代码，而 MailCode 的核心价值在于后台守护进程（CLI 即可满足所有操作）。

目标用户（开发者/SRE）本地都有 Python 环境，源码保护也不需要。改为 pip 分发后安装更快、迭代更快、维护更轻。

## 设计

### 整体方案

**总原则：不损失任何现有功能。** 所有非 TUI 的命令（serve / notify / config / health / session / scheduler / webhook / plugin / project）保持不变。

1. **`mailcode/` → `mailcode/`**：重命名源码目录，全局替换导入路径，使其成为标准的 pip 安装包
2. **移除 TUI**：删除 `tui/` 目录、`cli.py` 中的 tui 子命令、TUI 测试文件
3. **创建 `pyproject.toml`**：入口点 `mailcode = mailcode.cli:main`，零第三方依赖（TUI 移除后所有功能均使用标准库：smtplib、imaplib、asyncio 等）
4. **重写构建/安装脚本**：去掉 Nuitka，`build.sh` 改为 `python -m build` 构建 wheel，`install.sh` 改为 `pip install`
5. **清理残留**：删除 `dist-nuitka/`、`.nuitka-*`、`mailcode.egg-info/`

### 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 包名 | `mailcode` | 与 CLI 命令一致 |
| 源码目录 | `mailcode/`（保留旧名） | `mailcode/` 不是可发布的包名 |
| 第三方依赖 | 无 | TUI 移除后所有代码只用标准库 |
| 构建工具 | `setuptools`（`pyproject.toml`） | 零额外依赖，简单可靠 |
| CI 发布 | PyPI + GitHub Release | 两者并存，用户可通过 `pip install mailcode` 或下载 wheel 安装 |

### 数据流

pip 安装后用户操作不变：

```bash
# 安装前后一致
mailcode serve          # 启动 IMAP 监听
mailcode config init    # 初始化配置
mailcode session list   # 查看会话
mailcode --version      # 版本信息
```

唯一变化：不再有 `mailcode tui` 子命令。

### 接口变更

**移除的 CLI 子命令**：
- `mailcode tui` — 删除
- `mailcode tui config/sessions/health` — 删除

## 涉及文件

### 需要修改
- 所有 `.py` 文件（`from mailcode.` → `from mailcode.`）
- 所有 `.py` 测试文件（相同替换）
- `build.sh`
- `install.sh`
- `uninstall.sh`
- `prepare.sh`
- `release.sh`
- `.github/workflows/release.yml`
- `.gitignore`

### 需要删除
- `mailcode/tui/` 整个目录（~2000 行）
- `tests/unit/tui/` 整个目录
- `mailcode.egg-info/` 整个目录
- `dist-nuitka/` 整个目录（可选）
- `.nuitka-*` 目录

### 需要创建
- `pyproject.toml`

## 测试策略

- 现有单元测试适用于所有非 TUI 功能
- 不需要为 pip 安装新增测试
- 验证方式：`pip install -e .` → 运行 `mailcode --help` → 运行 `pytest tests/`

## 波及文档

- `docs/design-final/design.md` — 第 10 章"用户界面"需更新（移除 TUI 引用）
- `docs/design-final/tui-design.md` — 全文已无意义，可归档或删除
- `AGENTS.md` — 需移除 TUI 相关描述

## 风险与注意事项

- **`mailcode/` → `mailcode/` 重命名必须完整**：漏掉一个导入路径会导致运行时 `ModuleNotFoundError`。需要通过 grep 确认所有引用都已更新
- **任何 TUI 相关的代码都不要遗漏**：`cli.py` 中 `cmd_tui` 函数和第 370 行的延迟导入必须删除
- **`prepare.sh` 中的 `mailcode.pth` 路径注入**：需要更新以反映新的包名
