# astrbot_plugin_qing_image_source

## 仓库安装

在 AstrBot 插件管理中通过仓库地址安装：

```text
https://github.com/qing-yi-5427/astrbot_plugin_qing_image_source
```

插件元数据和入口位于仓库根目录；更新可通过 AstrBot 插件管理完成。

AstrBot 4.x 动漫图片来源搜索插件。支持“当前消息图片 + 关键词”和“引用图片 + 关键词”两种方式，使用 SauceNAO 搜索插画原出处、trace.moe 识别动画截图，并以 Ascii2D 作为兜底。

## 功能

- 群聊默认无需 @ Bot；是否要求 @ 可配置。
- 触发关键词、匹配方式和大小写规则可配置。
- 引用图片优先，当前图片回退；多图每次只查询第一张。
- 引用链缺图时可选使用 OneBot `get_msg` 回退。
- 自动路线：SauceNAO 优先，必要时查询 trace.moe；仍无高可信作品链接时回退 Ascii2D。
- 展示所属作品、角色、作者、相似度和作品链接（具体字段取决于搜索索引）；每条结果最多显示一个作品链接，不显示 SauceNAO 的额外来源页链接。
- 默认强制使用文字消息，无论文字长度都不经过 AstrBot 文本转图片。
- `sauce` / `saucenao` 强制 SauceNAO，`trace` / `tracemoe` / `搜番` 强制 trace.moe。
- QQ/OneBot 收到有效请求时给原消息添加可配置的 👌 回应。
- SHA-256 结果缓存、全局并发控制、用户/会话冷却、HTTP 重试。
- 不保存原图，不使用公开图床；Ascii2D 直接接收图片字节。

## 安装

将整个 `astrbot_plugin_qing_image_source` 目录放入 AstrBot 的 `data/plugins/`，然后在 WebUI 重载插件。AstrBot 会根据 `requirements.txt` 安装依赖。

插件要求 AstrBot `>=4.16,<5`。

## 配置

在 AstrBot WebUI 的插件配置中至少填写 `search.saucenao_api_key`。未填写时会跳过 SauceNAO，但 trace.moe 仍可工作。

Ascii2D 回退默认开启。它会自动建立会话；如果提示 Cloudflare 403，请在浏览器访问 Ascii2D 并通过验证，然后将 `_session_id`、`cf_clearance` Cookie 和该浏览器的 User-Agent 填入插件配置。Cookie 会失效，届时需要重新填写。

重要默认值：

- 智能关键词：`搜图`、`找出处`、`查来源`、`link`
- 群聊要求 @：关闭
- 关键词模式：`contains`
- 结果数：3
- 缓存：12 小时
- QQ 回应：`emoji_id=128076`、`emoji_type=2`（👌）
- SauceNAO 成人内容隐藏级别：2
- Ascii2D 自动回退：开启
- 始终使用文字消息：开启

## 使用

以下消息都会触发：

1. 发送图片，并在同一条消息写 `搜图`。
2. 引用一条图片消息，再发送 `link`。
3. 图片加 `sauce`，只查询 SauceNAO。
4. 引用动画截图并发送 `搜番`，只查询 trace.moe。

必须同时存在图片和关键词。只有关键词或只有图片时，插件不会接管消息。

## 管理命令

- `/搜图状态`：查看引擎配置、缓存统计和最近一次额度信息。
- `/搜图清缓存`：清空结构化结果缓存。
- `/link帮助`：显示当前关键词和用法。

上述命令仅管理员可用。

## 隐私与安全

- 图片直接上传至所启用的搜索服务（SauceNAO、trace.moe、Ascii2D），不会转存到 Catbox 等图床。
- 插件只保存图片 SHA-256 与结构化结果，不保存原图。
- 仅从 AstrBot 的 `Image` / `Reply.chain` 组件取图，不接受用户提供的任意 URL 或服务器文件路径。
- GIF 只使用第一帧；图片有体积、像素和真实格式校验。
- 疑似成人或低可信结果不发送缩略图。
- 群聊中的成人结果会隐藏标题和链接；私聊默认同样隐藏，可由管理员开启。

## 已知限制

- 某些平台适配器不会在 `Reply.chain` 中提供引用图片，此时无法查询该引用图。
- 表情回应目前只在提供 `set_msg_emoji_like` 的 QQ/OneBot 适配器上生效；其他平台静默跳过。
- SauceNAO 的不同索引字段并不完全一致，部分结果可能没有作者或可点击原链。
- Ascii2D 受 Cloudflare 保护，Cookie 失效时会跳过该回退并在结果提示中说明。

## 致谢与参考

本项目为独立实现，设计调研参考：

- [iona-s/astrbot_plugin_imgexploration](https://github.com/iona-s/astrbot_plugin_imgexploration)
- [PaloMiku/astrbot_plugin_search_tracemoe](https://github.com/PaloMiku/astrbot_plugin_search_tracemoe)
- [drdon1234/astrbot_plugin_img_rev_searcher](https://github.com/drdon1234/astrbot_plugin_img_rev_searcher)
- [kitUIN/PicImageSearch](https://github.com/kitUIN/PicImageSearch)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)

没有复制上述 AGPL 项目的源码。

## License

MIT
