# v1.2.0：Git 主题同步

当前版本可从公开 GitHub 仓库读取主题。默认仓库为 `qing-yi-5427/astrbot-plugins`，分支 `main`，目录 `t2i-themes`。在 WebUI 的本插件设置中启用「Git 主题同步」后，插件启动时在后台检查，默认每 30 分钟更新；间隔设为 0 则只启动/手动同步。

需要文转图服务 **astrbot-t2i-lite 2.1.0+**（`theme_protocol: 1`）。旧服务仍可用内置主题；同步会明确报告需要升级，不会把未知主题发送给旧服务。

```text
/t2i sync
/t2i list
/t2i use 原神
/t2i global use genshin
/t2i rollback
```

中文也支持 `/图样式 同步`、`/图样式 回退`。所有命令仍仅管理员可用。

- 主题源码与资源按 Git commit 固定读取并校验 blob；在本地实际试渲染，全部通过后原子替换缓存，下载/验证失败保留原版本。
- 同名 ID 更新会自动替换对应样式；新 ID 自动出现在 `/t2i list`，不需要修改插件名单或 Docker。
- 普通聊天不读取 Git，不把主题源码加进 LLM 上下文，只在文转图 HTTP 请求中传递当前选用主题。
- 上一份有效缓存保留供 `/t2i rollback` 使用；回退后暂不再次应用那个 commit，等仓库新提交后继续更新。
- 同步不改人格、模型、历史上下文或会话覆盖。主题被移除/缓存不可用时，未知 ID 输出暂用 Codex，保存的会话选择不会被删除。
- `repository.json`、`repository.previous.json` 与 `styles.json` 均位于插件自己的 `data/plugin_data/astrbot_plugin_t2i_style/`，不应提交进 Git。

主题目录：[t2i-themes](../../t2i-themes/)。给其他 agent 的完整说明：[T2I_THEME_AUTHORING.md](../../docs/T2I_THEME_AUTHORING.md)。设计格式开放，只要求正文入口和可正常渲染，其他字段、头像、资源均可选。

新增设置：`repository_enabled`、`theme_repository`、`theme_repository_ref`、`theme_repository_path`、`theme_sync_on_start`、`theme_sync_interval_minutes`。默认 Git 同步关闭以便先配置服务，启用后才访问所选仓库。

以下基础控制、会话状态格式和真实回复模型记录保持兼容。升级 v1.2.0 时请停用旧独立「回复模型页脚」插件，继续只用合并版。

# 基础控制与真实模型页脚

为 **AstrBot 管理员**提供会话级与全局文转图主题、品牌、副标题和头像控制，并内置每条回复的真实模型页脚。**单独启用本插件即可，不再依赖独立 model_footer 插件**。只控制呈现与观察回复元数据，**不修改模型提供商、模型选择、AstrBot 全局文转图设置或其他插件**。

插件已在隔离的真实 AstrBot **4.28.2 / 4.29.4** 环境验证；服务升级与部署仍由管理员单独执行，插件保存成功不代表生产图片已经生效。测试只请求本机 loopback HTTP 服务，不连接 NAS、不发送 QQ 消息。

## 正式主题

| id | 中文别名 | 服务端默认头像 |
| --- | --- | --- |
| `codex` | 原版 | 原版头像 |
| `miku` | 初音 | 初音未来 |
| `genshin` | 原神 | 派蒙 |
| `starrail` | 铁道、星穹铁道 | 帕姆 |
| `zzz` | 绝区零 | 伊埃斯 |
| `rio` | 莉音 | 调月莉音 |

品牌、文案、图片均以已部署服务的主题默认值为准。插件不会在没有自定义设置时发送旧 `Amadeus` 名称或旧头像去覆盖主题默认值。

## 管理员命令

所有命令（包括列表和状态）都只允许 **AstrBot 管理员**。群管理员身份本身不授予权限。没有注册任何 LLM tool，模型不能通过工具调用更改样式。

```text
/t2i list
/t2i status
/t2i use miku
/t2i use 初音
/t2i name 我的机器人
/t2i subtitle 今天也要元气满满
/t2i avatar 123456789
/t2i avatar default
/t2i name default
/t2i subtitle default
/t2i reset
```

默认只修改当前会话的完整 `unified_msg_origin`，不会使用裸群号混淆平台/机器人，不提供指定其他会话的控制接口。

```text
/t2i global use genshin
/t2i global name 全局机器人名称
/t2i global subtitle 全局副标题
/t2i global avatar 123456789
/t2i global avatar default
/t2i global reset
/t2i global status
```

显式会话前缀为 `/t2i session ...`。中文入口为 `/图样式`；子命令别名：`列表`、`状态`、`使用`、`名称`、`副标题`、`头像`、`重置`、`帮助`；作用域别名：`全局`、`会话`。例如：

```text
/图样式 全局 使用 原神
/图样式 会话 使用 莉音
/图样式 头像 默认
```

- `name` 最多 64 字，`subtitle` 最多 96 字，支持空格，拒绝控制字符。
- `name default`、`subtitle default` 或省略其文本：恢复**当前主题默认文案**，即使上级作用域有自定义也不会继续继承。`default/theme/默认/主题` 是保留恢复关键字，不能作为相应自定义文案。
- **`avatar default` 恢复当前主题头像**，屏蔽全局或 WebUI 的自定义头像，不是回到原版头像。`theme/默认/主题` 同义。
- `avatar inherit`（或 `头像 继承`）仅移除当前作用域的头像设置，重新继承上级自定义或主题默认头像。
- `reset` 清除当前会话的全部覆盖，重新继承全局/WebUI 默认；`global reset` 只清除管理员全局覆盖，不改变各会话覆盖。
- 切换主题不清除已明确设置的自定义名称、副标题、头像。如希望主题完整默认形象，先 `reset`，或者分别执行上述 `default`。
- 本版不提供 `preview`：不接受任意预览 URL，也不会额外发送会话私密正文。可先切换主题，再按正常 LLM 文转图流程验证。

## WebUI 配置与持久化

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `theme` | `codex` | 正式六主题之一 |
| `brand_name` | 空 | 空值继承主题默认名称；非空最多 64 字 |
| `subtitle` | 空 | 空值继承主题默认文案；非空最多 96 字 |
| `avatar_path` | 空 | 空值继承主题头像；非空是管理员配置的本机图片路径 |
| `show_avatar` | `true` | 默认显示头像 |
| `show_footer` | `true` | 显示真实回复模型页脚；2.0.2 主题服务已取消独立头像署名条 |
| `service_url` | 空 | 可选能力探测地址回退值；优先使用真实渲染地址，不会更改 AstrBot 地址 |

例如管理员可将 `service_url` 设为 `http://t2i-lite:8000/text2img`；通常不必填写，因为插件能读取真实网络策略及会话配置。本项只是探测回退，**不会配置 AstrBot 的实际渲染地址**。为兼容本地开发早期配置，代码仍接受旧 `brand` 键；正式 WebUI 使用 `brand_name`，两者同时存在时后者优先。

有效值优先级：**内置默认 < WebUI 默认 < 管理员全局覆盖 < 当前会话覆盖**。恢复主题默认会在本作用域保存一个内部空标记，屏蔽上级自定义，但发送给服务时省略对应个性化字段。

WebUI 更新后按正常流程重载插件；既有管理员指令覆盖不会被配置更新静默清空。数据仅保存到 AstrBot 提供的 `data/plugin_data/astrbot_plugin_t2i_style/styles.json`，不写全局配置。采用串行事务、临时文件原子替换、文件权限 `0600`；重载会读取覆盖。JSON 损坏或字段非法时初始化失败，不覆盖原数据。上限为 256 个会话覆盖、16 MiB 状态文件。状态不输出头像数据、QQ 号、本机文件路径或服务凭据。

## 服务升级要求、能力探测与生效条件

**必须先升级到支持 `style_protocol: 1` 的六主题服务。** 旧服务可能忽略 `t2i_style`，本插件不会把“设置已保存”当作“效果已生效”。管理员修改成功后和 `status` 都会明确显示服务能力结果。服务不支持、不可达、响应非法或未配置地址时不注入任何主题字段，保留 AstrBot 原有渲染行为，并提示未启用主题注入。

探测目标按照真实渲染顺序确定：

1. AstrBot **4.29.4** 的本次 `endpoint` 参数非空时，只探测该地址，忽略不会被使用的全局地址池。
2. 未显式指定时，探测 `NetworkRenderStrategy.endpoints` 的实际候选池，不额外探测未使用的 `BASE_RENDER_URL`。
3. 候选池为空才回退到 `BASE_RENDER_URL`；没有可用策略地址才使用可选 `service_url`。没有地址绝不会因为空列表判定“全部支持”。
4. 池内的每个真实 fallback 候选都必须支持协议 1，防止个性化字段随机发到旧服务。插件不修改、筛选或重排 AstrBot 的候选地址。

地址只来自管理员服务配置/真实渲染参数，不从聊天接受地址。允许 HTTP/HTTPS，拒绝 URL 用户名密码、query、fragment。探测时去掉末尾 `/generate`、补齐 `/text2img`，请求其 `/health`；这个规范化仅用于探测，不能修复管理员填错的 AstrBot 实际地址。

健康响应要求整数 `style_protocol: 1`，不接受布尔值或字符串。超时为总计 4 秒、连接/读取 2 秒；响应上限 16 KiB；不跟随重定向，不读取代理环境配置，不发送对话正文/头像/认证信息。探测结果按实际地址集合缓存 60 秒，并发合并请求；更新服务后可等待缓存过期或重载插件。

只有以下条件**同时满足**才注入 `tmpldata.t2i_style`：

- 正处于 `ResultDecorateStage.process` 的当前事件推进；
- 当前结果 `is_llm_result()` 为真；
- 经 `NetworkRenderStrategy.render_custom_template` 渲染；
- HTML 包含精确标记 `<div class="brand-name">Amadeus</div>`，即现有 codex-brand 模板；
- 本次真实目标确认支持协议 1。

其他插件 HTML 模板、普通插件结果、没有事件上下文的直接渲染和本地策略不增强。需要 AstrBot 自身启用文转图、使用 remote 策略、选择现有 codex-brand HTML 模板，且文本达到其阈值。本插件不能让低于阈值或关闭文转图的文本自动变成图片。

## 统一注入协议与真实模型来源

v1.1.0 使用一个统一桥接，只有三处全局包装：`ResultDecorateStage.process` 绑定事件、`NetworkRenderStrategy.render_custom_template` 同时注入模型与主题、`ToolLoopAgentRunner._iter_llm_responses` 观察实际 runner 回复。没有内部套两套 Stage/Network 桥接。

主题字段默认只发送：

```json
{"template": "miku", "show_avatar": true, "show_footer": true}
```

只有明确自定义时才额外发送 `brand_name`、`subtitle`、`avatar_base64`，从而让服务按每个主题选择默认品牌与头像。头像字段是经过验证的纯 base64 PNG/JPEG/WebP，不传 URL、本地路径或数据文件名。

桥接复制调用方字典，保留其他字段，并将本次事件的模型记录作为 `tmpldata.model_name`，与 `t2i_style` 并列，不放入样式对象。模型只来自以下路径：

1. 普通回复通过 `@filter.on_llm_response(priority=-10000)` 的 `remember_response_model(event, response)` 回调观察 `raw_completion.model`；没有时观察 `raw_completion.model_version`，同时支持字典和对象形态。
2. 工具循环在真实 `ToolLoopAgentRunner._iter_llm_responses` 中，优先观察相同 raw 字段；原始响应未报告模型时，使用**该次 runner 正在调用的请求模型**。主候选允许 `runner.req.model`；fallback 的 `include_model=False` 不沿用主候选请求覆盖，而只读取已经选中的 `runner.provider.get_model()`。
3. 不读取 Context 的默认 provider，不按发送时的全局设置猜模型；没有可靠记录时明确显示 `未提供模型信息`。普通 callback 不用任何请求外的默认值兜底。

错误响应、流式中间 chunk 不覆盖有效模型记录；模型名称去除控制/格式字符并最多 160 字。每个事件单独保存在兼容旧插件的 `_qing_reply_model_footer` extra key，不存在全局“最后一次模型”。与暂时共存的旧 model_footer 可以共享本次事件记录，但正常使用只需保留本插件。

主题与模型只在当前 codex-brand 的 LLM 结果中增强，不改变其他 HTML、自定义插件结果或无上下文的直接渲染。模型记录不依赖主题服务能力探测；即使旧服务未声明 style_protocol、主题注入被禁用，仍可传入可靠的模型元数据。`show_footer=false` 由主题服务隐藏模型页脚，不删除真实模型记录。T2I 2.0.2 起头像改为新生成插画，服务不再追加独立素材署名条。

隔离验证可使用真实 `LLMResponse`：

```python
from astrbot.api.provider import LLMResponse

await plugin.remember_response_model(
    event,
    LLMResponse(
        role="assistant",
        completion_text="Local test",
        raw_completion={"model": "actual-provider-model"},
    ),
)
```

回调只记录元数据，不调用模型、不发送消息。插件卸载后即使持有旧回调引用再调用，也不会新增模型记录。

## 头像安全与异步边界

- QQ 号必须为 5–12 位 ASCII 数字、非零开头，固定构造 `https://q1.qlogo.cn/g?b=qq&nk=...&s=640`；不接受其他 URL。
- aiohttp 异步下载，总计 10 秒、连接/读取 4 秒，不跟随重定向，不读取代理环境配置。
- 同时限制响应声明长度与流式累计长度为 512000 字节；检查状态、MIME、真实 PNG/JPEG/WebP 签名及 Pillow 完整解码；拒绝损坏、动画和任一边超过 1024 像素的图片。
- 图片解码、管理员本机头像读取、持久化读写都在线程执行，不阻塞 event loop。
- 本地头像只来自 WebUI 配置；聊天不能读取任意文件。扩展名必须为 `.png/.jpg/.jpeg/.webp`，打开前先校验，拒绝最终路径符号链接、非普通文件和超限文件。请只指向可信图片，不配置任何凭据文件。
- 服务仍应再次校验并重编码头像；不得根据这些数据抓 URL 或读取任意路径。

## 从 v1.0.0 + 独立页脚迁移到 v1.1.0

升级仍使用相同插件标识 `astrbot_plugin_t2i_style` 和原数据目录，`styles.json` 的 `version: 1`、全部控制命令与配置键保持兼容，**不执行重置或数据格式迁移**。已有会话主题、名称、副标题和头像覆盖全部保留。插件本身不会自动停用、删除旧插件，也不会部署或修改生产配置。

建议由管理员按顺序操作：

1. 备份当前主题插件源码、其 WebUI 配置、专用 `styles.json`，以及主题/独立页脚插件的启用状态。不要混入任何凭据或其他用户未提交工作。
2. 使用 AstrBot 正常安装/更新接口，以**相同插件名**替换为 v1.1.0；不要手动删除插件数据目录，不运行 `reset`。
3. 确认“文转图主题与模型页脚”v1.1.0 已初始化成功，保留短暂与旧页脚并存的窗口是允许的。
4. 在 AstrBot 插件管理中**停用** `astrbot_plugin_model_footer`，等待其 `terminate()` 完成；**不要删除**旧插件和备份，以便回滚。
5. 确认只启用合并插件时，管理员 `status` 仍显示原设置，正常回复/工具循环回复的模型页脚来自真实模型元数据，多会话不串。优先在隔离进程调用 normal callback→Stage→服务验证，不需要为迁移测试发送 QQ 消息。
6. 若在长期进程中反复交错热重载，正常重启 AstrBot 可清理旧的失活包装层。主题服务仍沿用已部署的协议 1 服务，无需为本次合并更改六主题协议。

回滚时先停用合并 v1.1.0，通过正常接口恢复已备份的主题 v1.0.0，再**重新启用**旧 `astrbot_plugin_model_footer`；必要时重启 AstrBot 清理旧链。不要删除现有 `styles.json`；若确需恢复旧数据备份，应先保存升级后合法的新设置，避免回滚覆盖新修改。

## 卸载与热重载

用同一个 ContextVar 在每次 Stage async-generator 推进与关闭时绑定当前事件，yield 前还原，支持并发会话隔离、异常、取消和提前关闭。每次安装的 Stage、Network、Runner 共用独立且永久可撤销的生命周期标记；卸载后旧 wrapper 即使被其他桥接恢复，也不会再记录模型或注入主题/模型字段，重新启用不会复活旧标记。探测或 runner 流执行过程中卸载，也不会在请求完成后重新记录/注入；底层 async generator 在取消、提前结束时仍正确关闭。

只有自身 wrapper 仍在顶层时才恢复原函数；不改别人的 wrapper、闭包或私有补丁记录。现有 model_footer 仅顶层恢复，因此交错卸载可能保留失活 pass-through wrapper。四种安装/卸载次序已验证无主题污染、保留仍启用的模型字段；重复交错热重载可能累积失活调用层，必要时正常重启进程清理。这是与当前 model_footer 卸载实现共存的已知限制，不通过破坏其他插件链路强行摘除。

## 验证与交接边界

隔离安装真实 PyPI `astrbot==4.28.2` 与 `astrbot==4.29.4`，不是仅用 fake stub 检查导入。v1.1.0 两版本最终均为 **150 passed**；各 6 条警告来自第三方依赖的已知弃用/转义提示，不是本插件失败。完整测试覆盖：

- 权限过滤器真实注册、管理员/普通用户双重校验、GreedyStr 多词与空参数、中文命令；
- 六主题白名单/别名、服务默认字段省略、主题默认头像屏蔽上级自定义、持久化重读、并发更新、失败回滚与取消一致性；
- PNG/JPEG/WebP、坏图、动画、大尺寸、超长/错误 MIME、禁重定向、超时、QQ 输入限制、凭据扩展名拒绝、解码不阻塞；
- 能力探测旧服务拒绝、限长与超时、缓存合并、空候选拒绝、实际候选与 4.29.4 每次请求 endpoint 优先；
- 普通 raw model/model_version 回调、工具循环真实 runner 及 provider fallback、错误/chunk 忽略、当前 runner 请求优先且不读取全局默认 provider；
- ContextVar 并发/异常/提前关闭/取消，Stage/Network/Runner 卸载后失活、重新启用不复活旧 wrapper、与旧 model_footer 四种安装/卸载组合；
- v1.0.0 的现有 `styles.json` 初始化/卸载后逐字节不变，无需重置或数据迁移；
- **真实 ResultDecorateStage → HtmlRenderer → NetworkRenderStrategy → aiohttp loopback HTTP**：检查生成请求的实际 JSON、会话隔离、主题默认字段省略、每条真实模型元数据和结果图片组件。只替换模板加载与本机 HTTP 服务边界，不替换核心 AstrBot 渲染代码，不发送消息。

该插件目录的 Ruff format/check 也独立通过。服务实际 Typst 排版、头像素材与生产容器升级由服务任务/管理员另外验收，本插件测试不冒称完成生产部署或真实 QQ 收发。

开发环境 `.validation/`、缓存和字节码均已忽略，**不要复制/打包开发环境**。为避免 AstrBot 导入时生成数据到集合仓库其他位置，测试从隔离目录执行：

```bash
cd .validation
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pytest -c ../pytest.ini ../tests -q \
  --basetemp=pytest-428 -o cache_dir=pytest-cache428
PYTHONDONTWRITEBYTECODE=1 venv429/bin/python -m pytest -c ../pytest.ini ../tests -q \
  --basetemp=pytest-429 -o cache_dir=pytest-cache429
cd ..
.validation/venv429/bin/ruff format --check --exclude .validation .
.validation/venv429/bin/ruff check --exclude .validation .
```

接口核验依据包括对应版本已安装源码，以及公开源码：

- https://github.com/AstrBotDevs/AstrBot/blob/v4.28.2/astrbot/core/pipeline/result_decorate/stage.py
- https://github.com/AstrBotDevs/AstrBot/blob/v4.28.2/astrbot/core/star/filter/command.py
- https://github.com/AstrBotDevs/AstrBot/blob/v4.28.2/astrbot/core/star/star_tools.py
- https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/utils/t2i/network_strategy.py
- https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/utils/t2i/renderer.py
