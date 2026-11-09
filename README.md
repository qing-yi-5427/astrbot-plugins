# AstrBot 插件集合

统一维护 10 个自用插件，各插件保留独立目录、元数据与原 Git 历史。此仓库不包含第三方插件本体；记忆召回修复插件按其现有 AGPL-3.0 许可保留上游方法来源说明。

| 插件 | 目录 / 安装包名 |
| --- | --- |
| [上下文图片大小限制](plugins/astrbot_plugin_context_image_limiter/README.md) | `astrbot_plugin_context_image_limiter` |
| [LLM 请求表情回应](plugins/astrbot_plugin_get_emoji_reply/README.md) | `astrbot_plugin_get_emoji_reply` |
| [QQ 群 @ 唤醒 LLM](plugins/astrbot_plugin_mention_llm/README.md) | `astrbot_plugin_mention_llm` |
| [GPT Image 生图](plugins/astrbot_plugin_imagegen/README.md) | `astrbot_plugin_imagegen` |
| [LivingMemory 召回兼容修复](plugins/astrbot_plugin_memory_recall_fix/README.md) | `astrbot_plugin_memory_recall_fix` |
| [回复模型页脚](plugins/astrbot_plugin_model_footer/README.md) | `astrbot_plugin_model_footer` |
| [文转图主题与模型页脚](plugins/astrbot_plugin_t2i_style/README.md) | `astrbot_plugin_t2i_style` |
| [图片来源搜索](plugins/astrbot_plugin_qing_image_source/README.md) | `astrbot_plugin_qing_image_source` |
| [随机选择](plugins/astrbot_plugin_roll/README.md) | `astrbot_plugin_roll` |
| [Tavily / AnySearch 结果限制](plugins/tavily_result_limiter/README.md) | `tavily_result_limiter` |

## 安装与更新

1. 从 [Releases](https://github.com/qing-yi-5427/astrbot-plugins/releases) 下载所需插件的 ZIP。
2. 在 AstrBot 插件管理中选择本地 ZIP 上传，再重载插件。
3. 更新时重新上传该插件的新 ZIP。已有配置沿用原插件名称；按各插件说明配置。

也可将 `plugins/` 内所需的单个插件目录复制到 AstrBot 的 `data/plugins/`。不要把本仓库根目录或 GitHub 子目录网页地址填入“通过 Git 仓库安装”；AstrBot 需要单插件根目录。为避免自动更新错误，子插件元数据不设置 `repo`。

## 打包

```sh
python3 scripts/package_plugins.py
```

在 `dist/` 生成 10 个独立 ZIP，`metadata.yaml` 和插件入口位于各 ZIP 根目录。打包仅使用 Git 跟踪文件，排除测试、缓存、开发环境和文档辅助目录。

## T2I 主题与 agent 文档

[独立主题目录](t2i-themes/) 收录 Codex、初音、原神/派蒙、星铁/帕姆、绝区零/伊埃斯、莉音六套参考。新增主题只需添加一个含 `theme.typ` 的文件夹，名称、头像和其他资源可选；插件会自动识别。

**交给其他 agent 的文档：[T2I_THEME_AUTHORING.md](docs/T2I_THEME_AUTHORING.md)**。不强制视觉格式，能正确渲染即可。

启用主题插件的 Git 同步，管理员 `/t2i sync` 即可拉取同一 commit 的主题、试渲染并应用。普通回复只读本地缓存；更新相同 ID 会替换对应主题，`/t2i rollback` 可回退。需要 T2I 服务 2.1.0+，不必为每次主题修改重建 Docker。

## 独立服务

[astrbot-t2i-lite](https://github.com/qing-yi-5427/astrbot-t2i-lite) 是独立 Docker 文转图服务，继续在单独仓库维护。

## 同步记录

2026-10-08：插件代码已与 NAS 运行目录核对。整合仅调整目录、安装说明和元数据仓库字段，不改变插件业务逻辑，也不修改运行中的 AstrBot。
