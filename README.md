# astrbot_plugin_get_emoji_reply

## 仓库安装

在 AstrBot 插件管理中通过仓库地址安装：

```text
https://github.com/qing-yi-5427/astrbot_plugin_get_emoji_reply
```

插件元数据和入口位于仓库根目录；更新可通过 AstrBot 插件管理完成。

面向 **AstrBot + LLOneBot（OneBot 11）** 的 `/aa` LLM 调用表情回应插件。

只有当原始群消息匹配 `/aa` 或以 `/aa ` 开头，并且 AstrBot 已经构建并触发实际 LLM 请求时，插件才会通过 LLOneBot 的 `set_msg_emoji_like` 接口，为原消息添加 QQ 系统表情 `124`（“OK”手势，用于表达 GET）。普通群消息、其他命令以及未进入 LLM 请求阶段的 `/aa` 消息都不会触发。

## 匹配规则

满足以下文本边界、并实际进入 LLM 请求阶段时会回应：

```text
/aa 你好
/aa 帮我总结这段内容
```

不会回应：

```text
普通群消息
/aaa
/aa_test
/help
```

单独发送 `/aa` 是否会回应，取决于 AstrBot 是否能据此构建实际 LLM 请求；若因内容为空、模型未配置、会话禁用 AI 或被其他插件拦截而没有进入 `on_llm_request`，本插件不会点表情。

## 功能

- 仅回应由 `/aa` 触发并已进入 AstrBot `on_llm_request` 阶段的 QQ 群消息
- 仅处理 AstrBot 的 `aiocqhttp` / OneBot 11 平台
- 使用 LLOneBot 的 `set_msg_emoji_like` 参数格式
- 自动忽略：
  - 普通群消息和其他命令
  - 私聊消息（LLOneBot 的该接口仅支持群消息）
  - 通知和请求事件
  - Bot 自己发送的消息
  - 无效消息 ID
- 表情回应失败不会影响 LLM 调用、其他插件或正常回复

## 前置条件

1. AstrBot `>= 4.0`
2. QQ 平台通过 AstrBot 的 `aiocqhttp` 适配器连接
3. OneBot 实现为 [LLOneBot / LuckyLilliaBot](https://github.com/LLOneBot/LuckyLilliaBot)
4. LLOneBot 已启用 OneBot 11，并与 AstrBot 正常连接
5. AstrBot 已正确配置 `/aa` 调用 LLM（例如全局唤醒前缀 `/` 配合 LLM 额外唤醒前缀 `aa`）

## 安装

### 方法一：上传 ZIP

1. 使用本项目生成的 `astrbot_plugin_get_emoji_reply.zip`。
2. 打开 AstrBot WebUI。
3. 进入 **插件管理**。
4. 点击右下角的 **添加插件** 按钮。
5. 选择本地上传方式并上传 ZIP。
6. 安装完成后重载或启用插件。

ZIP 中的 `metadata.yaml`、`main.py`、`_conf_schema.json` 和 `README.md` 位于压缩包根目录。

### 方法二：手动安装

在 AstrBot 的插件目录中创建：

```text
AstrBot/data/plugins/astrbot_plugin_get_emoji_reply/
```

将以下文件复制进去：

```text
astrbot_plugin_get_emoji_reply/
├── main.py
├── metadata.yaml
├── _conf_schema.json
└── README.md
```

然后在 AstrBot WebUI 中重载插件，或重启 AstrBot。

### 方法三：从 Git 仓库安装

在 AstrBot 的添加插件界面填写 `https://github.com/qing-yi-5427/astrbot_plugin_get_emoji_reply`。

## 配置

| 字段 | 默认值 | 说明 |
|---|---:|---|
| `emoji_id` | `124` | LLOneBot 使用的 QQ 表情 ID |

常用值：

- `124`：QQ“OK”手势，默认用于表达 GET
- `428`：QQ“收到”
- `76`：QQ“赞”
- `201`：QQ“点赞”

修改配置后重载插件。

## 工作方式

插件不监听普通消息事件，而是注册 AstrBot 的 `on_llm_request` 钩子。
因此普通群消息不会因为本插件而唤醒 Bot。钩子使用最低优先级：如果其他
LLM 请求钩子先终止请求，本插件也不会添加表情。

进入钩子后，插件从未被 AstrBot 改写的
`event.message_obj.message_str` 读取原消息，并匹配：

```regex
^/aa(?:\s|$)
```

只有同时满足“实际 LLM 请求已构建”和“原消息以 `/aa` 为完整命令边界”
时，才执行 LLOneBot 回应。

```python
await event.bot.call_action(
    "set_msg_emoji_like",
    message_id=message_id,
    emoji_id="124",
    set=True,
    self_id=self_id,
)
```

LLOneBot 的 `set_msg_emoji_like` 只支持群消息，因此本插件不会尝试回应私聊消息。

## 排错

### 插件未出现在 AstrBot 中

确认插件目录或 ZIP 根目录包含：

- `metadata.yaml`
- `main.py`
- `_conf_schema.json`
- `README.md`

并确认 `metadata.yaml` 使用 UTF-8 编码。

### 发送 `/aa` 后没有表情回应

1. 确认 `/aa` 能让 AstrBot 实际调用 LLM；推荐配置为全局唤醒前缀 `/`、LLM 额外唤醒前缀 `aa`。
2. 确认 AstrBot 平台类型为 `aiocqhttp`。
3. 确认协议端是 LLOneBot 且 OneBot 11 连接正常。
4. 确认测试的是群消息，不是私聊。
5. 使用 `/aa 你好` 测试，不要使用空内容、`/aaa` 或 `/aa_test`。
6. 查看 AstrBot 插件日志中是否有 `LLOneBot 消息 ... 表情回应失败`。
7. 尝试把 `emoji_id` 改为 `428` 或 `76`，排除当前 QQ 版本不接受某个表情 ID 的情况。

## 参考

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [LLOneBot / LuckyLilliaBot](https://github.com/LLOneBot/LuckyLilliaBot)
- 灵感与接口用法参考：[astrbot_plugin_qq_group_daily_analysis](https://github.com/SXP-Simon/astrbot_plugin_qq_group_daily_analysis)

## 同步记录

2026-10-08：已与 NAS 正在运行的 AstrBot Docker 插件目录核对，插件代码及配置结构一致。
