> 本插件已统一维护于 [astrbot-plugins](https://github.com/qing-yi-5427/astrbot-plugins)。请从该仓库 [Releases](https://github.com/qing-yi-5427/astrbot-plugins/releases) 下载本插件 ZIP 后本地上传安装，或复制当前子目录到 `data/plugins/`。多插件仓库不能直接作为单插件 Git 地址安装；更新请重新上传 ZIP。

# LLOneBot /aa 随机表情回应


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

### 从合集获取安装包

从统一仓库 Releases 下载本插件的独立 ZIP，在 AstrBot 中本地上传安装。

## 配置（v1.4.1）

默认开启随机模式，每条符合条件的消息从表情池中等概率选一个表情。不同消息可能随机选到相同表情；同一事件成功回应后不会重复添加。

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `random_enabled` | `true` | 关闭后恢复固定表情 |
| `emoji_ids` | `["14", "21", "63", "66", "74", "76", "99", "124", "201", "428"]` | 随机候选 QQ 表情 ID 列表，可在插件配置中修改 |
| `emoji_id` | `124` | 固定模式或空池的回退表情，保留旧设置 |

升级时即使旧配置只有 `emoji_id`，也会启用默认随机池。无效条目会忽略，重复 ID 去重；列表为空、格式错误或没有有效 ID 时回退到固定表情。数字格式有效不代表 QQ 一定支持该表情，如回应失败请从池中移除该 ID。修改配置后重载插件。

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
    emoji_id=chosen_emoji_id,
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
7. 如某些表情不被 QQ 接受，从 `emoji_ids` 中移除；或关闭 `random_enabled`，使用固定 `emoji_id=124` 排查。

## 参考

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [LLOneBot / LuckyLilliaBot](https://github.com/LLOneBot/LuckyLilliaBot)
- 灵感与接口用法参考：[astrbot_plugin_qq_group_daily_analysis](https://github.com/SXP-Simon/astrbot_plugin_qq_group_daily_analysis)

## 同步记录

2026-10-08：已与 NAS 正在运行的 AstrBot Docker 插件目录核对，插件代码及配置结构一致。

## 更新记录

v1.4.0：加入默认启用的随机表情池，保留 `/aa`、群聊、实际 LLM 请求、重复事件及错误隔离限制。不发送独立聊天消息。

v1.4.1：默认随机池扩充到 10 个 QQ 表情；保留用户自定义表情池。
