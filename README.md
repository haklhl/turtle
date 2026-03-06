# 🐢 Sea Turtle

**轻量级、可自托管的个人 AI Agent 系统**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Sea Turtle 是一个基于 Python 的轻量级 AI Agent 系统，支持多 LLM 提供商、Telegram/Discord 通道、本地 Shell 执行、多 Agent 沙箱隔离、自动上下文压缩和心跳任务检查。

## ✨ 特性

- **多 LLM 支持** — Google Gemini、OpenAI、Claude、Grok、OpenRouter、Codex CLI 本地模型
- **多通道** — Telegram Bot、Discord Bot
- **图片消息** — Telegram 可接收图片输入，Agent 也可回传本地图片/文件
- **情绪贴纸** — 可选启用 Telegram sticker 资产库，Agent 通过 `STICKER_EMOTION` 触发贴纸发送
- **多 Agent** — 独立工作目录、独立通信账号、独立沙箱
- **本地 Shell** — 安全执行本地命令，危险命令需确认
- **三级沙箱** — normal / confined（可联网）/ restricted（无网络）
- **自动上下文压缩** — 对话过长时自动摘要压缩
- **会话持久化** — 按 source + chat_id + user_id 落盘恢复上下文
- **耗时统计** — `/context` 和 `/status` 显示最近一次与平均回复耗时
- **心跳任务** — 定期检查结构化 `task.json`，仅处理待办/进行中任务
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
| `/prompt` | 导出当前会话最终 System Prompt `.txt`（owner） |
| `/tasks` | 查看最近 20 条任务 |
| `/restart` | 重启 Agent |
| `/usage` | Token 用量和费用 |
| `/status` | Agent 状态 |
| `/model list [provider]` | 列出可用模型 |
| `/model <name>` | 切换模型 |
| `/effort` | 查看当前 Codex 思考深度 |
| `/effort list` | 列出可用 Codex 思考深度 |
| `/effort <level>` | 设置 Codex 思考深度 |
| `/help` | 帮助 |

Telegram 普通文本、图片、文件和带说明文字的附件消息都会转发给对应 Agent。入站附件会先下载到对应 agent workspace 下的 `.incoming/telegram/`，并按保留期自动清理。若 Agent 需要把本机已有图片或文件发回 Telegram，可在最终回复里输出一行 `ATTACH: /absolute/path/to/file`。`/model` 和 `/effort` 的切换会写回本地 `config.json`，因此重启后仍然生效。Heartbeat 任务使用结构化 `task.json`，仅处理 `pending` / `in_progress`，完成后回写任务状态并向 owner 推送摘要。

## 🏗️ 架构

```
systemd/launchd (服务守护)
  └── Daemon (主进程)
        ├── Telegram Listener
        ├── Discord Listener
        ├── Command Router (/命令 → 主进程, 普通消息 → Agent)
        ├── Agent 子进程 (default) — 独立工作目录, 沙箱隔离
        ├── Agent 子进程 (work)    — 独立工作目录, 沙箱隔离
        └── Heartbeat (定时检查 task.json)
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
├── agents/                 # Agent 工作区（默认在仓库外）
│   └── default/
│       ├── rules.md
│       ├── skills.md
│       ├── memory.md
│       ├── task.json
│       └── .shell_history
└── venv/                   # 虚拟环境
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
