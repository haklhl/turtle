# 配置参数详解

Sea Turtle 使用 JSON 格式配置文件，默认位置：`~/.sea_turtle/config.json`

配置文件搜索顺序：
1. `--config` 参数指定的路径
2. `./config.json`（当前目录）
3. `~/.sea_turtle/config.json`
4. `/etc/sea_turtle/config.json`

## global — 全局配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `log_level` | string | `"info"` | 日志级别 (debug/info/warning/error) |
| `log_file` | string | `"~/.sea_turtle/logs/daemon.log"` | 主进程日志文件 |
| `data_dir` | string | `"~/.sea_turtle"` | 数据目录 |
| `default_agent` | string | `"default"` | 默认 Agent ID |
| `pid_file` | string | `"~/.sea_turtle/daemon.pid"` | PID 文件 |
| `socket_path` | string | `"~/.sea_turtle/daemon.sock"` | Unix Socket 路径 |

## llm — LLM 配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `default_provider` | string | `"google"` | 默认 LLM 提供商 |
| `default_model` | string | `"gemini-2.5-flash"` | 默认模型 |
| `temperature` | float | `0.7` | 采样温度 |
| `max_output_tokens` | int | `8192` | 最大输出 token 数 |
| `providers` | object | — | 各提供商配置 |

### providers 子项

每个提供商包含：

| 参数 | 说明 |
|------|------|
| `api_key_env` | API Key 的环境变量名 |

支持的提供商：`google`, `openai`, `anthropic`, `openrouter`, `xai`

## context — 上下文管理

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_tokens` | int | `200000` | 最大上下文 token 数（20万） |
| `compress_threshold_ratio` | float | `0.7` | 触发压缩的阈值（占 max_tokens 比例） |
| `compress_target_ratio` | float | `0.3` | 压缩后目标大小（占 max_tokens 比例） |
| `compress_model` | string | `"gemini-2.0-flash"` | 用于压缩摘要的模型 |

## shell — Shell 执行

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 是否启用 Shell 工具 |
| `timeout_seconds` | int | `30` | 命令超时时间 |
| `max_output_chars` | int | `10000` | 输出截取最大字符数 |
| `dangerous_commands` | list | `["rm","sudo",...]` | 需确认的危险命令 |
| `blocked_commands` | list | `["rm -rf /"]` | 绝对禁止的命令 |
| `history_max_entries` | int | `10000` | 历史最大条数 |
| `history_max_file_size_mb` | int | `50` | 历史文件最大 MB |
| `history_record_output` | bool | `true` | 是否记录命令输出 |
| `history_output_max_chars` | int | `500` | 记录输出截取字符数 |

## telegram / discord — 通道配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `false` | 是否启用 |
| `bot_token_env` | string | — | Bot Token 环境变量名 |
| `allowed_user_ids` | list | `[]` | 允许的用户 ID（空=全部允许） |

## heartbeat — 心跳

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 是否启用 |
| `interval_seconds` | int | `300` | 检查间隔（秒） |

## token_billing — Token 计费

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 是否启用 |
| `log_file` | string | `"token_usage.json"` | 用量日志文件 |

## logging — 日志

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `level` | string | `"info"` | 日志级别 |
| `max_file_size_mb` | int | `10` | 单文件最大 MB |
| `backup_count` | int | `3` | 保留轮转文件数 |
| `format` | string | `"[{asctime}]..."` | 日志格式 |

## agents — Agent 配置

每个 Agent 的配置项：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | string | `"Turtle"` | Agent 显示名称 |
| `human_name` | string | `"Human"` | 用户称谓 |
| `workspace` | string | `"./agents/<id>"` | 工作目录 |
| `model` | string | 继承全局 | 使用的模型 |
| `tools` | list | `["shell","memory","task"]` | 启用的工具 |
| `sandbox` | string | `"confined"` | 沙箱模式 (normal/confined/restricted) |
| `telegram` | object | — | Agent 专属 Telegram 配置 |
| `discord` | object | — | Agent 专属 Discord 配置 |
