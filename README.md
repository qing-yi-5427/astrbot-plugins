# GPT Image 生图插件

## 仓库安装

在 AstrBot 插件管理中通过仓库地址安装：

```text
https://github.com/qing-yi-5427/astrbot_plugin_imagegen
```

插件元数据和入口位于仓库根目录；更新可通过 AstrBot 插件管理完成。

为 AstrBot 提供两种入口，生成的真实图片会发送到发起请求的会话：

- **指令**：`/i 一只趴在窗边的橘猫，水彩风格`。也支持 `/imagegen` 和 `/生图`。提示词可包含空格和换行。
- **自然语言 / LLM 工具**：通过你已有的 `/aa` 对话入口发送“生成一张猫图”。对话模型收到 `astrbot_generate_image` 工具后，自行识别请求、整理提示词并调用；插件完成生图和图片发送，执行结果返回给模型。

生图服务与对话服务相互独立：对话模型负责理解请求和调用工具，GPT Image 模型负责生成图片。

## 安装

要求 AstrBot >= 4.28.0、Python >= 3.11。已对照 AstrBot 当前官方接口实现；推荐使用你部署环境支持的最新稳定版。

1. 将整个 `astrbot_plugin_imagegen` 目录放入 AstrBot 的 `data/plugins/`，或使用插件管理页面的本地 ZIP 上传功能安装随附的 ZIP。
2. 在 AstrBot 插件管理页面安装依赖并重载/启用插件；常规安装由 AstrBot 根据 `requirements.txt` 处理依赖。如果手动安装目录且出现 `aiohttp` 缺失，请在 **AstrBot 所使用的 Python 环境**执行 `python -m pip install -r data/plugins/astrbot_plugin_imagegen/requirements.txt`。
3. 打开插件配置，填写 `base_url`、`api_key` 和 `model`，保存并重载。
4. 先用 `/i 一只猫` 确认服务与会话图片发送正常，再测试自然语言调用。



## 连接配置

配置在 AstrBot 管理页面可视化编辑，定义见 `_conf_schema.json`。示例值（在页面逐项填写，不要用此片段覆盖其他配置）：

```json
{
  "base_url": "https://api.openai.com/v1",
  "images_path": "/images/generations",
  "api_key": "填写你的生图服务 Key",
  "model": "gpt-image-2",
  "size": "1024x1024",
  "quality": "auto",
  "output_format": "png",
  "n": 1,
  "timeout": 300
}
```

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `base_url` | `https://api.openai.com/v1` | 可换为 OpenAI 兼容网关；通常需要包含 `/v1`。也接受完整生图地址 |
| `images_path` | `/images/generations` | 接口路径，拼接到 base_url；已含此路径时不重复拼接 |
| `api_key` | 空 | 留空读取 `OPENAI_API_KEY`；均为空时不发送鉴权头。官方服务需有效 Key |
| `model` | `gpt-image-2` | 任意服务端支持的 GPT Image 模型名/映射名，不固定模型白名单 |
| `size` | `auto` | `宽x高` 或 `auto`，允许自定义尺寸，由服务端验证模型支持情况 |
| `quality` | `auto` | `low` / `medium` / `high` / `auto`；支持的新版模型还可用 `xhigh` / `max` |
| `output_format` | `png` | `png` / `jpeg` / `webp` |
| `background` | `auto` | `auto` / `opaque` / `transparent`；透明背景须用 PNG/WebP 且模型支持 |
| `n` | `1` | 一次生成 1–10 张，指令和工具共用 |
| `timeout` | `300` | 生图和下载的总超时，1–1800 秒 |
| `proxy` | 空 | HTTP(S) 代理；留空遵循代理环境变量 |
| `max_image_mb` | `20` | 单张图片上限，1–100 MiB |
| `max_concurrent` | `2` | 全局并发上限 1–10，同一会话只允许一个任务；重载生效 |
| `show_progress` | `true` | 发送“正在生成图片” |
| `inject_tool_hint` | `true` | 仅在明确生图请求且工具可用时，补充调用提示 |

接口使用 `POST /images/generations`，请求包含 `model`、`prompt`、`n`、`size`、`quality`、`output_format`、`background`。GPT Image 返回 `data[].b64_json`，插件也兼容网关返回 `data[].url`。不会向 GPT Image 发送旧模型专用的 `response_format` 参数。

网关必须支持 OpenAI Images API 与这些参数；仅提供 `/chat/completions` 或 `/responses` 的网关不能直接接入此插件。若网关对尺寸、数量、背景或质量参数有限制，调整配置以匹配服务端能力。

## 让 `/aa` 对话自动生图

插件不会重新实现或接管 `/aa`。自动调用需要满足：

1. `/aa` 使用支持 **function calling / tools** 的对话模型，并将 AstrBot 可用工具传入请求。
2. 在 AstrBot 的工具管理中启用 `astrbot_generate_image`；若当前人格限制工具白名单，将此工具加入白名单。
3. 使用明确的生图请求，例如 `/aa 帮我生成一张猫图，日系插画风格`。

工具描述已包含使用条件和发送行为，默认也会通过 `on_llm_request` 补充提示。模型是否调用仍取决于对话模型和入口的工具支持；不支持工具的模型可直接使用 `/i`。

### 调用流程与普通聊天隔离

v1.2.0 仅根据当前用户消息识别明确生图指令，例如“生成一张猫图”“根据你对群友的记忆画一张印象画像”。生图提示要求模型先按用户要求、对话和相关资料整理完整 prompt，再作为工具参数调用 `astrbot_generate_image`；插件仅接收这份 prompt、调用生图 API 并发送图片。

没有明确生图指令时，当前请求会隐藏生图工具，保留记忆、搜索等原有工具；不追加任何生图提示，不调用生图接口，不发送额外消息，也不修改人格、记忆片段、回复或历史。只复制当前请求的工具集，不改写共享工具注册表。

生图意图不从记忆或历史推断。“介绍你记忆里的小糖豆”“这张图片好看吗”“如何画猫”“再来一张”等不会开启生图流程；后续生图请明确说“再画一张”。常见中英文句式以保守识别为主，不保证识别所有措辞。

已移除旧版本的原文兜底生图、回复替换及历史同步钩子。旧配置中的 `fallback_on_no_tool` 不再使用。如果模型未调用工具，插件不会绕过模型把原消息当作 prompt 生图。涉及群友或过去事实时，生图提示要求模型参考已有记忆，资料不足且检索工具可用时先检索；检索行为由对话模型决定。

如果 `/aa` 来自另一个插件且它自己发起请求，该插件必须传入包含本工具的工具集，并正确执行工具调用。若它绕过 AstrBot 标准 Agent 流程，单靠安装本插件无法让它获得工具能力。

可在所用人格中加入提示：

> 用户明确要求画图、生成图片时，调用 astrbot_generate_image，将用户要求与相关上下文整理为完整 prompt。工具会直接把图片发送给用户。只有工具确认发送成功后才回复已完成；失败时如实说明，不要只输出图片描述或虚构链接。

## 行为与故障排查

- `/i` 的命令前缀遵循 AstrBot 的唤醒词配置；若全局前缀改为 `!`，使用 `!i`。
- 同一条消息重复调用相同提示词时复用结果，避免重复生图；失败也不会自动重试。超时可能发生在服务已经生成/计费之后，需要用户自行决定重试。
- 多张图逐张发送。发送中断时，工具结果记录 `generated`、`sent` 和 `partial` 状态，模型能知道实际已发送数量。
- 图片通过 AstrBot `Image.fromBytes` 发送，无需共享磁盘或暴露公网文件服务。图片链接会先下载，API Key 只用于生图请求，不会传给图片 CDN。
- 错误信息与日志不记录 API Key、完整提示词、接口响应正文或 Base64 图片。
- HTTP 401/403：检查 Key 和账户模型权限；404：检查 API 前缀、路径和模型；400：检查参数与内容限制；429：检查配额/余额；超时：检查网关和网络、适当增大 `timeout`。
- 有工具却不调用：检查人格白名单、对话模型的工具支持以及 `/aa` 的实现；先通过 `/i` 区分接口故障与模型调用问题。
- 生图完成却收不到：检查 AstrBot 平台连接与平台的图片/文件大小限制，可尝试 JPEG 或减小尺寸。

## 开发验证

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
ruff check .
ruff format --check .
```

测试使用本地 HTTP 服务验证真实请求、Base64 解码、图片下载、鉴权隔离、大小限制和超时，并使用 AstrBot API 测试替身验证指令、工具、会话发送和失败处理。无需 API Key，不会调用付费接口。实际生图模型与聊天平台的端到端验证需要在你的 AstrBot 实例配置服务后完成。

参考：[AstrBot 插件 AI 与工具接口](https://docs.astrbot.app/dev/star/guides/ai.html)、[AstrBot 消息发送](https://docs.astrbot.app/dev/star/guides/send-message.html)、[OpenAI Image API](https://developers.openai.com/api/docs/guides/image-generation)。

## 同步记录

2026-10-08：首次上传本地开发版本；尚未与 NAS 正在运行的 Docker 插件目录逐文件核对。仓库不包含运行配置、聊天数据或密钥。
