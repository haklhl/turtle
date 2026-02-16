# CLI 命令参考

主命令: **`seaturtle`** (简写 `st`)

## 服务管理

### `seaturtle start`
启动守护进程。如果已在运行则提示。

### `seaturtle stop`
向守护进程发送 SIGTERM 停止信号。

### `seaturtle status`
显示守护进程状态和所有已配置 Agent 的信息。

### `seaturtle logs [agent_id] [--follow]`
查看日志。不指定 agent_id 则显示主进程日志。
- `--follow` / `-f`: 实时跟踪日志输出

## Agent 管理

### `seaturtle agent list`
列出所有已配置的 Agent 及其状态。

### `seaturtle agent add <id> [--name NAME] [--model MODEL] [--sandbox MODE]`
创建新 Agent。
- `--name`: Agent 显示名称（默认同 ID）
- `--model`: LLM 模型（默认继承全局配置）
- `--sandbox`: 沙箱模式 normal/confined/restricted（默认 confined）

自动创建工作目录和初始文件（rules.md, skills.md, memory.md, task.md）。

### `seaturtle agent del <id> [--force]`
从配置中删除 Agent。
- `--force`: 跳过确认提示
- 注意：工作目录文件不会被自动删除

### `seaturtle agent start <id>`
启动指定 Agent（需要守护进程运行中）。

### `seaturtle agent stop <id>`
停止指定 Agent。

### `seaturtle agent restart <id>`
重启指定 Agent。

### `seaturtle agent info <id>`
显示 Agent 详细配置信息。

## 模型管理

### `seaturtle model list [provider]`
列出可用模型。可选按提供商过滤：
```bash
seaturtle model list          # 所有模型
seaturtle model list google   # 仅 Google 模型
seaturtle model list openai   # 仅 OpenAI 模型
seaturtle model list anthropic
seaturtle model list xai
```

### `seaturtle model set <agent_id> <model_name>`
切换指定 Agent 的模型。修改保存到配置文件，重启 Agent 后生效。

## 配置管理

### `seaturtle config show`
显示当前完整配置（JSON 格式）。

### `seaturtle config edit`
使用 `$EDITOR`（默认 vi）打开配置文件编辑。

### `seaturtle config validate`
校验配置文件，报告错误和警告。

## 安装与维护

### `seaturtle install-service`
注册为系统服务：
- Linux: 生成 systemd unit 文件
- macOS: 生成 launchd plist 文件

### `seaturtle uninstall-service`
移除系统服务注册。

### `seaturtle update [--check]`
从 GitHub 检查最新版本并升级。
- `--check`: 仅检查，不安装

### `seaturtle doctor`
环境诊断：检查 Python 版本、依赖安装、配置文件、数据目录。

### `seaturtle onboard`
交互式安装向导：选择 LLM 提供商、配置 API Key、设置 Agent、生成配置文件。
