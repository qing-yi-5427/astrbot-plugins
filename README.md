# AstrBot 插件集合

统一维护 8 个自用插件，各插件保留独立目录、元数据与原 Git 历史。此仓库不包含第三方插件本体；记忆召回修复插件按其现有 AGPL-3.0 许可保留上游方法来源说明。

| 插件 | 目录 / 安装包名 |
| --- | --- |
| [上下文图片大小限制](plugins/astrbot_plugin_context_image_limiter/README.md) | `astrbot_plugin_context_image_limiter` |
| [LLM 请求表情回应](plugins/astrbot_plugin_get_emoji_reply/README.md) | `astrbot_plugin_get_emoji_reply` |
| [GPT Image 生图](plugins/astrbot_plugin_imagegen/README.md) | `astrbot_plugin_imagegen` |
| [LivingMemory 召回兼容修复](plugins/astrbot_plugin_memory_recall_fix/README.md) | `astrbot_plugin_memory_recall_fix` |
| [回复模型页脚](plugins/astrbot_plugin_model_footer/README.md) | `astrbot_plugin_model_footer` |
| [图片来源搜索](plugins/astrbot_plugin_qing_image_source/README.md) | `astrbot_plugin_qing_image_source` |
| [随机选择](plugins/astrbot_plugin_roll/README.md) | `astrbot_plugin_roll` |
| [Tavily / AnySearch 结果限制](plugins/tavily_result_limiter/README.md) | `tavily_result_limiter` |

## 安装与更新

1. 登录有权访问本私有仓库的 GitHub 账号，从 [Releases](https://github.com/qing-yi-5427/astrbot-plugins/releases) 下载所需插件的 ZIP。
2. 在 AstrBot 插件管理中选择本地 ZIP 上传，再重载插件。
3. 更新时重新上传该插件的新 ZIP。已有配置沿用原插件名称；按各插件说明配置。

也可将 `plugins/` 内所需的单个插件目录复制到 AstrBot 的 `data/plugins/`。不要把本仓库根目录或 GitHub 子目录网页地址填入“通过 Git 仓库安装”；AstrBot 需要单插件根目录。为避免自动更新错误，子插件元数据不设置 `repo`。

## 打包

```sh
python3 scripts/package_plugins.py
```

在 `dist/` 生成 8 个独立 ZIP，`metadata.yaml` 和插件入口位于各 ZIP 根目录。打包仅使用 Git 跟踪文件，排除测试、缓存、开发环境和文档辅助目录。

## 独立服务

[astrbot-t2i-lite](https://github.com/qing-yi-5427/astrbot-t2i-lite) 是独立 Docker 文转图服务，继续在单独仓库维护。

## 同步记录

2026-10-08：插件代码已与 NAS 运行目录核对。整合仅调整目录、安装说明和元数据仓库字段，不改变插件业务逻辑，也不修改运行中的 AstrBot。
