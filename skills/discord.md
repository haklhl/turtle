# Discord Skills

## Discord 历史检索

- 当消息来源是 Discord，且需要查看频道结构、读取最近历史消息、或搜索更早的 Discord 历史时，优先使用本地脚本 `scripts/discord_tool`。
- 当用户问“刚才谁说了什么”“这个频道最近在聊什么”“上次讨论到哪了”“某个人提过什么”时，不要凭印象回答，先读或搜索 Discord 历史。
- 常用形式：
  - `scripts/discord_tool channel-info --channel-id <channel_id>`
  - `scripts/discord_tool read --channel-id <channel_id> --limit 20`
  - `scripts/discord_tool search --guild-id <guild_id> --query "关键词" --limit 10`
- 先运行 `scripts/discord_tool --help` 或子命令 `--help` 查看参数，再执行。
- 这类检索优先用于 Discord 渠道，不要在 Telegram 场景里无关调用。
- 如果没有先查历史，就不要装作知道 Discord 频道里更早发生过什么。

## Discord Embed

- 当需要在 Discord 里发送状态卡片、阶段总结、结构化摘要、结果面板、简短报告封面时，优先考虑使用 Discord embed，而不是纯文本长段落。
- 用法是在最终回复中附加 `DISCORD_EMBED: {...}` 单行 JSON，或 `DISCORD_EMBED_JSON:` 代码块；正文文本保持简短，把结构化信息放进 embed。
- 一次回复里只输出一个 `DISCORD_EMBED` 或 `DISCORD_EMBED_JSON` 指令块；如果需要多张卡片，在同一个 JSON 里使用 `{"embeds":[... ]}`，不要输出多个独立的 embed 指令块。
- Discord embed 常用长度限制：
  - `title` 最多 256 字符
  - `description` 最多 4096 字符
  - 每个 `field.name` 最多 256 字符
  - 每个 `field.value` 最多 1024 字符
  - 最多 25 个 `fields`
  - `footer.text` 最多 2048 字符
  - `author.name` 最多 256 字符
  - 单个 embed 总字符数不要超过 6000
- 一条 Discord 消息最多可带 10 个 embeds，但默认优先保持在 1 到 3 个，避免过度堆叠。

## Discord Components V2 / Modal

- 如果需要按钮、选择器、分隔线、文本块等 Discord Components V2，在最终回复中附加 `DISCORD_COMPONENTS: {...}` 单行 JSON，或 `DISCORD_COMPONENTS_JSON:` 代码块。
- Discord 组件这里强制按 V2 语义使用，不要设计旧式 V1 组件布局。
- 不要把 Discord components 和 embed 混在同一条回复里；如果既要说明文字又要组件，把说明文字做成 `text_display`，或者直接把普通正文写在前面，框架会自动转成前置 `text_display`。
- Modal 不能像普通消息一样直接发送；如果要让用户填写表单，先发送组件，再在按钮或选择器的 `action` 里使用 `{"type":"open_modal","modal":{...}}`。
- 如果用户是在 Discord 里要一个交互式表单或面板，优先用“组件 + modal”结构，而不是让用户手敲长段格式化文本。
- Discord Components V2 / Modal 速查：
  - 顶层写法：
    - `DISCORD_COMPONENTS: {...}`
    - 或 `DISCORD_COMPONENTS_JSON:` 后跟 JSON
  - 顶层结构：
    - `{"components":[ ... ]}`
    - 也可直接传组件数组，但优先统一用对象包一层
  - 常用组件类型：
    - `text_display`
      - 字段：`content`
    - `button`
      - 字段：`label`, `style`, `disabled`, `url`, `emoji`, `row`, `action`
      - `style` 常用：`primary`, `secondary`, `success`, `danger`, `link`
      - 有 `url` 时是跳转按钮，不走回调
    - `select`
      - 字段：`placeholder`, `min_values`, `max_values`, `options`, `disabled`, `row`, `action`
      - `options` 每项常用：`label`, `value`, `description`, `emoji`, `default`
    - `separator`
      - 字段：`visible`, `spacing`
      - `spacing` 常用：`small`, `large`
    - `section`
      - 字段：`children`, `accessory`
      - `children` 可放字符串或子组件；`accessory` 通常放按钮、缩略图等
    - `container`
      - 字段：`children`, `accent_color`, `spoiler`
    - `thumbnail`
      - 字段：`media`, `description`, `spoiler`
    - `media_gallery`
      - 字段：`items`
      - `items` 每项常用：`media`, `description`, `spoiler`
  - `action` 常用类型：
    - `{"type":"route_message", "ack":"...", "template":"..."}`：把交互事件转成一条新消息交给我继续处理
    - `{"type":"respond", "content":"...", "ephemeral":true}`：仅回一条交互提示
    - `{"type":"open_modal", "modal":{...}}`：点击后打开 modal
  - `route_message` 模板里可用的占位符：
    - `{{agent_id}}`
    - `{{channel_id}}`
    - `{{guild_id}}`
    - `{{user_id}}`
    - `{{user_name}}`
    - `{{values}}`
    - `{{component_label}}`
    - `{{component_custom_id}}`
    - `{{fields.<custom_id>}}`
  - Modal 顶层结构：
    - `{"title":"...", "components":[ ... ], "submit":{...}}`
  - Modal 内目前只放 `text_input`
    - 字段：`custom_id`, `label`, `style`, `placeholder`, `default`, `required`, `min_length`, `max_length`, `row`
    - `style` 常用：`short`, `paragraph`
  - 重要限制：
    - Components 这里强制按 V2 语义使用
    - 不要把 `components` 和 `embed` 放在同一条回复里
    - 如果既要说明文字又要组件，优先用 `text_display`
    - Modal 不能直接发送，只能通过按钮或选择器的 `open_modal` action 打开
