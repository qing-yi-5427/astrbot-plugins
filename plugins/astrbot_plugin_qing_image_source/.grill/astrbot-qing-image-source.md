# Grill: AstrBot 动漫图片来源搜索插件
Date: 2026-09-02

## Intent
实现一个面向 AstrBot 4.x 的图片来源搜索插件。用户在群聊或私聊中发送关键词并附带图片，或引用一条含图片的消息时，插件自动查询二次元插画/动画截图来源并返回可点击结果。

## Constraints
- 工程名为 `astrbot_plugin_qing_image_source`，显示名为“动漫搜图”。
- 必须同时存在图片和配置的关键词才触发；群聊是否必须 @ Bot 可配置，默认不要求。
- 引用图片优先于当前消息图片；多图只处理第一张并提示。
- MVP 仅使用 SauceNAO 和 trace.moe 官方异步 HTTP API。
- 不把图片上传到公共图床，不接受用户任意 URL 或服务器路径。
- 不保存原图；缓存仅保存 SHA-256、结构化结果和过期时间。
- 不发送“正在搜图”文本；QQ/OneBot 支持时在触发消息上贴可配置的 👌，失败静默降级。
- 疑似成人或低可信结果不得发送缩略图。

## Key decisions
- Decision: 群聊默认不要求 @，但 `require_at_in_group` 可配置。 Reason: 图片加关键词已经足够降低误触发。 Alternative considered: 群聊强制 @。
- Decision: 关键词列表和匹配方式可配置，默认不区分大小写的包含匹配。 Reason: 兼顾“帮我搜图”等自然表达。 Alternative considered: 仅完整文本匹配。
- Decision: 引用图优先、当前图回退，一次只查一张。 Reason: 引用动作意图更明确，且可控制 API 额度。 Alternative considered: 批量查询所有图片。
- Decision: 先 SauceNAO，结果不足或命中动画索引时再 trace.moe。 Reason: 分别覆盖插画原出处与动画场景。 Alternative considered: 默认并发查询全部引擎。
- Decision: SauceNAO/trace.moe 使用分引擎阈值映射为高可信、可能、低可信，不直接横向比较原始分数。 Reason: 两个引擎的分数语义不同。
- Decision: 最多展示三个候选，仅最高可信且安全的结果显示缩略图。 Reason: 避免群聊刷屏与成人内容泄露。
- Decision: QQ/OneBot 表情回应默认 `emoji_id=128076`、`emoji_type=2` 并保留；不支持的平台静默跳过。 Reason: AstrBot 没有统一的插件级 reaction API。
- Decision: 缓存默认 12 小时，带全局并发限制及用户/会话冷却。 Reason: 节省额度并控制滥用。
- Decision: 提供管理员命令 `/搜图状态`、`/搜图清缓存`、`/搜图帮助`。 Reason: 便于观察配置、额度和缓存，不暴露密钥。
- Decision: 使用 MIT License，自主实现并在 README 标注参考项目。 Reason: 避免复制 AGPL 项目源码带来的许可耦合。

## Surfaced assumptions
- 当前消息与 `Reply.chain` 中的图片通常已由 AstrBot 媒体预处理标准化；插件仍需验证最终文件。
- QQ/OneBot 适配器提供 `set_msg_emoji_like` 时才能贴表情。
- SauceNAO API Key 可能未配置；此时插件应跳过 SauceNAO，继续尝试 trace.moe。
- 某些平台不会把引用图片放入 `Reply.chain`；通用逻辑不能依赖解析 `raw_message`，OneBot 回退只能作为可选兼容路径。

## Out of scope
- Google Lens、Ascii2D、IQDB、AnimeTrace。
- LLM 自动工具调用。
- “先发送搜图命令，再等待下一张图片”的会话状态。
- 批量搜多张图片。
- 向 Catbox 等公开图床上传图片。
- 为 AstrBot 3.x 或已废弃 API 添加兼容分支。
