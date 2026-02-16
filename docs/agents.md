# 多 Agent 指南

## 概述

Sea Turtle 支持运行多个独立的 Agent，每个 Agent 拥有：
- 独立的工作目录
- 独立的通信账号（Telegram/Discord Bot）
- 独立的沙箱模式
- 独立的上下文和记忆
- 独立的 Shell 历史
- 独立的 Token 计费

## 创建 Agent

### CLI 方式

```bash
seaturtle agent add myagent --name "My Agent" --model gpt-4o --sandbox confined
```

### 手动配置

在 `config.json` 的 `agents` 中添加：

```json
{
  "agents": {
    "default": { ... },
    "myagent": {
      "name": "My Agent",
      "human_name": "Human",
      "workspace": "./agents/myagent",
      "model": "gpt-4o",
      "tools": ["shell", "memory", "task"],
      "sandbox": "confined",
      "telegram": {
        "bot_token_env": "TELEGRAM_BOT_TOKEN_MYAGENT",
        "allowed_user_ids": []
      },
      "discord": {
        "bot_token_env": "DISCORD_BOT_TOKEN_MYAGENT",
        "allowed_user_ids": []
      }
    }
  }
}
```

## Agent 工作目录

```
agents/myagent/
├── rules.md            # Agent 人设和行为规则
├── skills.md           # Agent 专属技能
├── memory.md           # 持久记忆
├── task.md             # 待办任务（心跳检查）
└── .shell_history      # Shell 命令历史
```

### rules.md

定义 Agent 的身份、行为规则和用户偏好。作为 system prompt 的一部分加载。

### skills.md

定义 Agent 的专属技能和工作流。格式参考：

```markdown
# Skills

## 代码审查

当用户要求代码审查时：
1. 先阅读完整文件
2. 检查代码风格、安全性、性能
3. 给出具体改进建议

## 日报生成

当用户要求生成日报时：
1. 检查今天的 git log
2. 汇总完成的工作
3. 生成 markdown 格式日报
```

### memory.md

Agent 的持久记忆。Agent 可以通过工具读写此文件，用于跨对话保存重要信息。

### task.md

待办任务列表。心跳系统会定期检查，发现未完成任务时通知 Agent。

```markdown
# Tasks

- [x] 完成项目初始化
- [ ] 编写单元测试
- [ ] 部署到生产环境
```

## 删除 Agent

```bash
seaturtle agent del myagent
```

这只会从配置中移除 Agent，工作目录文件不会被删除。

## 沙箱模式

每个 Agent 可独立配置沙箱级别：

| 模式 | 适用场景 |
|------|----------|
| `normal` | 完全信任的 Agent，需要系统级操作 |
| `confined` | 日常使用（默认），可联网但限制文件访问 |
| `restricted` | 处理敏感数据，完全隔离 |

## 通信账号

- 每个 Agent 可绑定独立的 Telegram/Discord Bot
- 也可多个 Agent 共享一个 Bot，通过 `/agent <id>` 切换
- `allowed_user_ids` 为空表示允许所有用户
