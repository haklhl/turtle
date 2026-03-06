# 🐢 Sea Turtle

**轻量级、可自托管的个人 AI Agent 系统**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Sea Turtle 是一个基于 Python 的轻量级 AI Agent 系统，支持多 LLM 提供商、Telegram/Discord 通道、本地 Shell 执行、多 Agent 沙箱隔离、自动上下文压缩和心跳任务检查。

## ✨ 特性

- **多 LLM 支持** — Google Gemini、OpenAI、Claude、Grok、OpenRouter、Codex CLI 本地模型
- **多通道** — Telegram Bot、Discord Bot
- **多 Agent** — 独立工作目录、独立通信账号、独立沙箱
- **本地 Shell** — 安全执行本地命令，危险命令需确认
- **三级沙箱** — normal / confined（可联网）/ restricted（无网络）
- **自动上下文压缩** — 对话过长时自动摘要压缩
- **心跳任务** — 定期检查 task.md，自动处理待办
- **Token 计费** — 自动统计用量和费用
- **Skills 系统** — 自定义 Agent 技能
- **一键安装** — 交互式配置向导
- **系统服务** — systemd (Linux) / launchd (macOS)
- **自动更新** — 从 GitHub 检查并升级

## 🚀 快速开始

### 一键安装

```bash
curl -sSL https://raw.githubusercontent.com/haklhl/turtle/main/setup.sh | bash
```

### 手动安装

```bash
pip install sea-turtle
seaturtle onboard    # 交互式配置
seaturtle start      # 启动
```

### 从源码安装

```bash
git clone https://github.com/haklhl/turtle.git
cd turtle
pip install -e .
seaturtle onboard
```

## 📋 CLI 命令

```bash
# 服务管理
seaturtle start                          # 启动守护进程
seaturtle stop                           # 停止守护进程
seaturtle status                         # 查看状态
seaturtle logs [agent_id] [--follow]     # 查看日志

# Agent 管理
seaturtle agent list                     # 列出所有 Agent
seaturtle agent add <id>                 # 创建新 Agent
seaturtle agent del <id>                 # 删除 Agent
seaturtle agent restart <id>             # 重启 Agent
seaturtle agent info <id>                # 查看 Agent 详情

# 模型管理
seaturtle model list [provider]          # 列出可用模型
seaturtle model set <agent_id> <model>   # 切换模型

# 配置
seaturtle config show                    # 显示配置
seaturtle config validate                # 校验配置

# 维护
seaturtle update [--check]               # 检查/安装更新
seaturtle doctor                         # 环境检查
seaturtle install-service                # 注册系统服务
```

## 💬 Telegram/Discord 命令

| 命令 | 说明 |
|------|------|
| `/start` | 初始化 |
| `/reset` | 重置上下文 |
| `/context` | 查看上下文统计 |
| `/restart` | 重启 Agent |
| `/usage` | Token 用量和费用 |
| `/status` | Agent 状态 |
| `/model list [provider]` | 列出可用模型 |
| `/model <name>` | 切换模型 |
| `/help` | 帮助 |

## 🏗️ 架构

```
systemd/launchd (服务守护)
  └── Daemon (主进程)
        ├── Telegram Listener
        ├── Discord Listener
        ├── Command Router (/命令 → 主进程, 普通消息 → Agent)
        ├── Agent 子进程 (default) — 独立工作目录, 沙箱隔离
        ├── Agent 子进程 (work)    — 独立工作目录, 沙箱隔离
        └── Heartbeat (定时检查 task.md)
```

## 🔒 沙箱模式

| 模式 | 网络 | 文件系统 | 进程管理 |
|------|------|----------|----------|
| **normal** | ✅ | ✅ | ✅ |
| **confined** (默认) | ✅ | ⚠️ 仅工作目录 | ❌ |
| **restricted** | ❌ | ⚠️ 仅工作目录 | ❌ |

## 🤖 支持的 LLM

| 提供商 | 模型 |
|--------|------|
| **Google** | gemini-2.5-pro, gemini-2.5-flash, gemini-2.0-flash, ... |
| **OpenAI** | gpt-4o, gpt-4.1, o3, o4-mini, ... |
| **Anthropic** | claude-sonnet-4, claude-3.5-sonnet, claude-3.5-haiku |
| **xAI** | grok-3, grok-3-mini |
| **OpenRouter** | 任意模型 (provider/model 格式) |
| **Codex CLI** | `codex-cloud`, `codex-5.4`, `codex-spark`, `codex-oss`（通过本地 `codex` 命令） |

## 📁 项目结构

```
~/.sea_turtle/              # 运行时数据
├── config.json             # 配置文件
├── logs/                   # 日志
│   ├── daemon.log
│   └── agents/<id>/agent.log
└── venv/                   # 虚拟环境

agents/                     # Agent 工作区
└── default/
    ├── rules.md            # Agent 人设
    ├── skills.md           # Agent 技能
    ├── memory.md           # 持久记忆
    ├── task.md             # 待办任务
    └── .shell_history      # 命令历史
```

## ⚙️ 配置

参见 [config.example.json](config.example.json) 获取完整配置示例。

## 📖 文档

- [系统架构](docs/architecture.md)
- [配置参数](docs/configuration.md)
- [多 Agent 指南](docs/agents.md)
- [CLI 命令参考](docs/cli.md)

## 📄 License

[MIT](LICENSE) © [haklhl](https://github.com/haklhl)
