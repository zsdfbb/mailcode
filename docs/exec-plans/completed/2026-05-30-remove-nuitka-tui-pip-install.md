# 移除 Nuitka 与 TUI，支持 pip 安装 — 执行计划

> 每个任务按目录划分，同一目录下的所有变更由一个 agent 完成。按依赖顺序分批执行。

## 上下文引用

参考设计计划：`docs/design-plans/2026-05-30-remove-nuitka-tui-pip-install.md`

## 关键变量

| 变量 | 值 | 说明 |
|------|-----|------|
| 源码目录 | `mailcode/` | `mailcode/` 重命名而来 |
| 包名 | `mailcode` | |
| CLI 入口 | `mailcode = mailcode.cli:main` | |
| 版本 | `0.3.0` | 从 `mailcode/__init__.py` 读取 |
| 第三方依赖 | 无 | TUI 移除后全标准库 |

## 任务清单

### Batch 1（必须最先执行）

#### Task 1: 重命名 `mailcode/` → `mailcode/`，更新所有导入路径

- **涉及目录**: 项目根目录（跨所有文件）
- **涉及文件**: 所有 `.py` 文件 + 若干 `.sh`/`.yml`/`.md` 文件
- **描述**: 
  1. `mv src mailcode`
  2. 在所有 `.py` 文件中将 `from mailcode.` → `from mailcode.`、`import mailcode.` → `import mailcode.`、`import src` → `import mailcode`（在 `prepare.sh` 的 pth 注入中使用）
  3. 更新 `.sh`/`.yml`/`.md` 中对 `mailcode/` 路径的引用
  4. 更新 `prepare.sh` 中的 `mailcode.pth` 路径注入
- **验证标准**:
  - [ ] `grep -rn "from src\|import src" mailcode/ tests/ --include="*.py"` 返回空（TUI 尚未删除，在 tui/ 内可存在的是 `from mailcode.` 前缀）
  - [ ] 实际检查确认所有 `import` 都已更新为 `mailcode.` 前缀
  - [ ] `ls mailcode/` 包含 `cli.py`、`config.py` 等原 `mailcode/` 下的文件

---

### Batch 2（与 Batch 1 无依赖，与 Batch 1 并发执行）

等等，Batch 2 依赖 Batch 1（因为文件路径变了）。所以 Batch 2 必须在 Batch 1 之后。

实际上，应该改为 Batch 2 在 Batch 1 完成后执行。

#### Task 2: 移除 TUI 代码（与 Task 3/4 可并发）

- **涉及目录**: `mailcode/tui/`、`tests/unit/tui/`、`mailcode/cli.py`
- **涉及文件**:
  - 删除 `mailcode/tui/` 整个目录
  - 删除 `tests/unit/tui/` 整个目录
  - 修改 `mailcode/cli.py`：删除 `cmd_tui` 函数和 `from mailcode.tui import run_tui` 导入
  - 修改 `mailcode/__init__.py`：如果有 `run_tui` 导出则删除
  - 删除 `mailcode/tui/__init__.py` 的引用（目录删除后自动消失）
- **验证标准**:
  - [ ] `mailcode/tui/` 目录已彻底删除
  - [ ] `tests/unit/tui/` 目录已彻底删除
  - [ ] `cli.py` 中无 `tui` 相关导入或 `cmd_tui` 函数
  - [ ] `cli.py` 中 `build_parser()` 不再有 `tui` 子命令
  - [ ] `grep -r "tui" mailcode/cli.py` 只返回版本/帮助等合法内容

#### Task 3: 创建 `pyproject.toml`

- **涉及目录**: 项目根目录
- **涉及文件**: 新增 `pyproject.toml`（根目录）
- **描述**:
  ```toml
  [build-system]
  requires = ["setuptools>=64"]
  build-backend = "setuptools.build_meta"

  [project]
  name = "mailcode"
  version = "0.3.0"
  description = "Email ↔ AI Agent bidirectional remote command bridge"
  requires-python = ">=3.9"
  license = {text = "MIT"}

  [project.scripts]
  mailcode = "mailcode.cli:main"

  [tool.setuptools.packages.find]
  include = ["mailcode*"]
  ```
- **验证标准**:
  - [ ] `pip install -e .` 成功
  - [ ] `mailcode --help` 输出正常
  - [ ] `mailcode --version` 输出 `mailcode 0.3.0`

#### Task 4: 清理 Nuitka 和旧 egg-info 残留

- **涉及目录**: 项目根目录
- **涉及文件**:
  - 删除 `mailcode.egg-info/` 目录
  - 更新 `.gitignore`：移除或注释 Nuitka 相关条目（`dist-nuitka/`、`.nuitka-build-venv/`、`.nuitka-ccache/`），可保留但加注释说明已弃用
  - 删除 `build.sh` 中对 ccache 的引用（后续 Task 5 会重写 build.sh，但先清理 gitignore）
- **验证标准**:
  - [ ] `mailcode.egg-info/` 已删除
  - [ ] `.gitignore` 已更新

---

### Batch 3（依赖 Batch 2，任务间可并发）

#### Task 5: 重写 `build.sh`

- **涉及目录**: 项目根目录
- **涉及文件**: `build.sh`
- **描述**:
  - 移除 Nuitka 编译逻辑
  - 移除 ccache 检测
  - 移除 lint + 测试（那是 CI 的事）
  - 新功能：`bash build.sh` → `pip install -e .`（开发安装）；`bash build.sh --dist` → `python -m build`（构建 wheel 到 dist/）
  - `build.sh --help` 显示用法
- **验证标准**:
  - [ ] `bash build.sh` 成功，`mailcode --help` 可用
  - [ ] `bash build.sh --dist` 成功，`dist/mailcode-0.3.0-py3-none-any.whl` 存在
  - [ ] 脚本中无 `nuitka`、`ccache` 等旧概念

#### Task 6: 重写 `install.sh`

- **涉及目录**: 项目根目录
- **涉及文件**: `install.sh`
- **描述**:
  - 移除 Nuitka 二进制复制逻辑
  - 改为 `pip install mailcode` 或 `pip install ./dist/mailcode-*.whl`
  - 保留：tmux 检查、配置部署、桥接插件部署、Claude hooks 合并、PATH 提示
  - 支持 `--local` 从本地 wheel 安装
- **验证标准**:
  - [ ] `bash install.sh --local` 从本地 wheel 安装成功
  - [ ] 安装后 `mailcode --version` 可用
  - [ ] 配置和插件部署功能正常

#### Task 7: 更新 `prepare.sh` 和 `uninstall.sh`

- **涉及目录**: 项目根目录
- **涉及文件**: `prepare.sh`、`uninstall.sh`
- **描述**:
  - `prepare.sh`：移除 `tmux` 兼容性填充层（libtmux 相关），更新 pth 路径为 `mailcode/`
  - `uninstall.sh`：改为 `pip uninstall mailcode -y` + 保留配置/插件清理
- **验证标准**:
  - [ ] `bash prepare.sh` 成功
  - [ ] `uninstall.sh` 成功卸载

---

### Batch 4（依赖 Batch 3）

#### Task 8: 更新 CI

- **涉及目录**: `.github/`
- **涉及文件**: `.github/workflows/release.yml`
- **描述**:
  - 移除 Nuitka 构建步骤
  - 改为 `python -m build` 构建 wheel
  - 添加 `twine upload` 步骤发布到 PyPI
  - 同时上传 wheel 到 GitHub Release
- **验证标准**:
  - [ ] CI 配置文件语法正确
  
关于发布到 PyPI 的密钥配置：在 CI 配置中注明需要仓库配置 `PYPI_TOKEN` 密钥。

#### Task 9: 更新文档

- **涉及目录**: 项目根目录、`docs/`
- **涉及文件**: `AGENTS.md`、`docs/design-final/design.md`、`README.md`
- **描述**:
  - `AGENTS.md`：移除 TUI 相关描述，更新项目结构
  - `docs/design-final/design.md`：第 10 章"用户界面"更新，移除 TUI 引用
  - `README.md`：安装方式改为 `pip install mailcode`
- **验证标准**:
  - [ ] 文档中无 TUI 或 Nuitka 引用残留

---

### Batch 5（最终验证）

#### Task 10: 运行完整测试套件

- **涉及目录**: `tests/`
- **描述**: 运行 `pytest tests/ -q` 确认所有测试通过
- **验证标准**:
  - [ ] `pytest tests/ -q` 全部通过

## 验证清单

- [ ] `pip install -e .` → `mailcode --help` 正常
- [ ] `bash build.sh` → 开发安装正常
- [ ] `bash build.sh --dist` → wheel 产出正常
- [ ] `bash install.sh` → 安装正常
- [ ] `pytest tests/ -q` 全部通过
- [ ] `mailcode serve --help` 正常（核心功能）
- [ ] `mailcode config --help` 正常
- [ ] 无 `from src` 或 `import src` 残留
- [ ] 无 `tui` 相关文件残留
- [ ] 无 Nuitka 相关文件残留
