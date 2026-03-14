# Telegram Skills

## Telegram 附件

- 当消息来源是 Telegram，如果需要把本机已有图片或文件发回去，在最终回复中单独输出一行：`ATTACH: /absolute/path/to/file`
- 一条回复里可以放多行 `ATTACH:`，每行一个绝对路径。
- 优先发送已经落在本地的最终产物，不要口头说“我会发文件”却不给 `ATTACH:`。
- Telegram 下载入站附件时有大小限制；超大文件不要假设已经成功进入工作区。

## Telegram 贴纸

- 如果当前 agent 开启了贴纸能力，并且回复里有明显情绪，可以在最终回复最后单独加一行：`STICKER_EMOTION: <emotion>`
- 常用情绪标签包括：`warm`, `happy`, `playful`, `embarrassed`, `angry`, `sad`, `calm`, `serious`, `surprised`, `tired`, `supportive`, `refuse`
- 一次回复最多放一个 `STICKER_EMOTION`
- 贴纸只是辅助，不要让贴纸替代正文

## Telegram 消息格式

- Telegram 发送层会把一小部分 Markdown 转成 HTML 再发出，但不要依赖复杂嵌套格式。
- 稳妥可用的写法：
  - `**粗体**`
  - `*斜体*`
  - `` `行内代码` ``
  - 三反引号代码块
  - `> 引用`
- 不成对的 Markdown 标记通常不会被自动补全渲染，所以不要输出半截格式标记。
- 回复要尽量清晰直白，避免为了样式堆太多格式。
