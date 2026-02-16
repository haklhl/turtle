# 系统架构

## 总体设计

Sea Turtle 采用三层架构：

```
┌─────────────────────────────────────────────────┐
│              systemd / launchd                   │
│              (服务守护层)                          │
├─────────────────────────────────────────────────┤
│            Daemon (主进程/守护进程)                 │
│  ┌──────────┬──────────┬──────────────────────┐ │
│  │ Telegram │ Discord  │  CLI (Unix Socket)   │ │
│  │ Listener │ Listener │  命令接口             │ │
│  └────┬─────┴────┬─────┴──────┬───────────────┘ │
│       │          │            │                  │
│  ┌────▼──────────▼────────────▼───────────────┐ │
│  │         Command Router (命令路由)           │ │
│  │  /reset /context /restart → 主进程处理      │ │
│  │  /model list/set → 主进程处理               │ │
│  │  普通消息 → 转发给对应 Agent 子进程          │ │
│  └────┬───────────────────────┬───────────────┘ │
│       │                       │                  │
│  ┌────▼─────────┐  ┌─────────▼──────────┐      │
│  │ Agent 子进程  │  │  Agent 子进程       │      │
│  │ (default)    │  │  (work)            │      │
│  │ 独立工作目录  │  │  独立工作目录       │      │
│  │ 独立沙箱模式  │  │  独立沙箱模式       │      │
│  └──────────────┘  └────────────────────┘      │
├─────────────────────────────────────────────────┤
│              Heartbeat (心跳)                     │
│  定时检查各 Agent 的 task.md                      │
└─────────────────────────────────────────────────┘
```

## 分层职责

### 服务守护层 (systemd/launchd)
- 确保主进程崩溃后自动重启
- 管理进程启动/停止
- 日志输出到 journal/文件

### 主进程 (Daemon)
- 托管通道监听器（Telegram/Discord）
- 命令路由：`/` 系统命令由主进程拦截处理
- Agent 生命周期管理（启动/停止/重启/崩溃恢复）
- 心跳调度
- CLI 命令处理（通过 Unix Socket）

### Agent 子进程
- 每个 Agent 运行在独立的 `multiprocessing.Process` 中
- 崩溃不影响主进程和其他 Agent
- 通过 `multiprocessing.Queue` 与主进程双向通信
- 拥有独立的：工作目录、上下文、记忆、Shell 历史、Token 计费

### 通道层
- Telegram/Discord 监听器由主进程持有
- 每个 Agent 可绑定独立的 Bot Token
- 也可多个 Agent 共享一个 Bot（通过 `/agent <id>` 切换）

## 进程通信

```
Daemon ──inbox Queue──> Agent Worker
Daemon <──outbox Queue── Agent Worker
```

消息格式：
```python
# 用户消息
{"type": "message", "content": "...", "source": "telegram", "chat_id": 123, "user_id": 456}

# 系统指令
{"type": "reset_context"}
{"type": "set_model", "model": "gpt-4o"}
{"type": "get_stats", "request_id": "uuid"}

# Agent 回复
{"type": "reply", "agent_id": "default", "content": "...", "source": "telegram", "chat_id": 123}

# 关闭信号
None  # Poison pill
```

## LLM 提供商架构

```
BaseLLMProvider (抽象基类)
├── GoogleProvider      (google-genai SDK)
├── OpenAIProvider      (openai SDK)
├── AnthropicProvider   (anthropic SDK)
├── OpenRouterProvider  (继承 OpenAIProvider, 自定义 base_url)
└── XAIProvider         (继承 OpenAIProvider, 自定义 base_url)
```

模型注册表 (`registry.py`) 维护预置模型列表和定价信息。

## System Prompt 组装顺序

1. **系统安全规范**（硬编码，不可覆盖）
2. **Agent 环境信息**（自动注入：工作目录、模型、沙箱等）
3. **Skills**（从 skills.md 加载，空则跳过）
4. **Memory**（从 memory.md 加载）
5. **Rules**（从 rules.md 加载，用户自定义）
