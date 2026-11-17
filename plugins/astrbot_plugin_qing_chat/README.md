# 对话管理

合并 @ 唤醒、随机表情、搜索与历史精简、图片预算、响应预算/同群排队、闲置新会话、记忆兼容修复和随机选择。只有本插件一个注册入口；各功能保留独立模块和开关。

**设置：AstrBot → 插件 → 对话管理 → 配置齿轮。** 支持原生分组表单，不需要新服务。默认沿用搜索 10 条（Tavily 最多 20）、上下文图片 10 MB、普通聊天 180 秒、工具任务 600 秒、闲置 8 小时；升级时导入当前实际配置。

保留 /aa 原生流程和原指令，不限制群聊轮数。代码兼容 AstrBot 4.29.4；元数据暂限 4.29 系列，内部接口变化时应先适配验证。普通消息不会因合并额外唤醒或调用生图。

首次升级请阅读 [设置与迁移说明](../../docs/QING_SUITES_SETTINGS.md)。先停用被替换的旧插件，再安装新包；保留旧 KV 与主题/缓存数据供新模块继续使用，不以卸载旧插件清数据的方式迁移。

内含 LivingMemory 2.7.0-beta.2 方法的兼容版本，保留上游 [lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory) 权利与来源，整个包以附带 AGPL-3.0 许可提供源码。LivingMemory 本体不打包、不改源码。
