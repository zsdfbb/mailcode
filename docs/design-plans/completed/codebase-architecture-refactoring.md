# Design Plan: MailCode 架构整理重构

Date: 2026-06-06
Status: Draft

## 1. 概要

MailCode 架构整理重构：删除 templates/ 死代码、提取 provider_presets.py、server.py 从 relay/ 提到顶层、提取 session_cli.py。

## 2. 动机

- templates/ 零引用，纯透传层
- server.py 在 relay/ 造成语义错位，且有自身 argparse 与 cli.py 重复
- cli.py 441 行混入 session 格式化逻辑，不便于测试
- config.py 同时承载 provider 预设数据和配置加载

## 3. 方案详情

### 3.1 删除 templates/
- 删除 mailcode/templates/ 整个目录
- pyproject.toml 移除 "templates/email_templates/*.txt"
- get_template() / list_templates() 零调用

### 3.2 提取 provider_presets.py
- 新建 mailcode/provider_presets.py — 移入 DOMAIN_PROVIDER_MAP、PROVIDER_PRESETS、detect_provider
- config.py 从新模块导入

### 3.3 server.py → 顶层
- 新建 mailcode/server.py — 去掉自身 argparse 和 __main__，暴露 run_serve(args)
- 删除 mailcode/relay/server.py
- cli.py:cmd_serve 改为调 server.run_serve(args)

### 3.4 提取 session_cli.py
- 新建 mailcode/session_cli.py — 移入 session 格式化/呈现函数
- cli.py:cmd_session 从 session_cli 导入转发

## 4. 波及文件清单

### 新增
- mailcode/provider_presets.py
- mailcode/server.py
- mailcode/session_cli.py

### 修改
- mailcode/config.py — 删三个定义，加 import
- mailcode/cli.py — 删 session 格式化函数 + server 和 session_cli 导入调整
- tests/unit/test_cli.py — 第 185 行 import 路径
- CLAUDE.md — 目录树和常用命令
- docs/design-final/design.md — 包结构描述
- pyproject.toml — 删 templates 打包规则

### 删除
- mailcode/templates/
- mailcode/relay/server.py

## 5. 不涉及
- relay/conversation_handler.py、email_listener.py、security.py 内容不变
- channels/ 不变
- config.py 的配置加载/缓存逻辑不变
- CLI 参数和行为不变

## 6. 测试策略
- 每步后 pytest tests/unit/ -q 验证
- 第 3 步需更新 test_cli.py 的 import 路径
