# LivingMemory 召回兼容修复

## 仓库安装

在 AstrBot 插件管理中通过仓库地址安装：

```text
https://github.com/qing-yi-5427/astrbot_plugin_memory_recall_fix
```

插件元数据和入口位于仓库根目录；更新可通过 AstrBot 插件管理完成。

独立安装的兼容插件，已针对 AstrBot 4.28.2 / LivingMemory 2.7.0-beta.2 验证。
不修改 LivingMemory 官方源码、人格提示词、生图插件或聊天历史。

- 文档、关系图和有效记忆原子融合后再进行一次最终重排序，保留明确的检索词命中。
- 自动注入和主动检索使用同一组有效事实；保留人物/主题的历史提及线索，明确区分别名线索与已确认身份。
- 将助手的检索状态与事实分开；保留用户纠正和不确定身份的警示，不将“暂时没查到”当作人物资料。
- Embedding 服务超时或连接错误时最多重试两次；其他存储错误不自动重复写入。
- 继续使用原插件的会话、人设和访问权限过滤，不增加跨会话检索。

## 配置

AstrBot 的百炼 Rerank 提供商使用完整文本重排序地址，例如：

`https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank`

模型为 `qwen3.7-text-rerank`。LivingMemory 中启用重排序，选择相同提供商 ID。
本次验证使用 30 个候选、最终返回 5 条；近期记忆保留数设为 0，避免无关新记录占用名额。
这些选项保存在 AstrBot 和 LivingMemory 的原有配置中，兼容插件不会在每次启动时强行覆盖配置。

## 更新和回滚

LivingMemory 可以继续通过原渠道更新，升级不会覆盖本插件目录。
兼容插件逐个校验相关方法的语法结构；若上游更改这些实现，会停用兼容修复并在日志中说明原因，保留官方行为。
这不保证以后所有版本自动兼容；新版需要重新适配和验证，尤其是上游已经修复同一问题时。

`/memory_fix_status` 显示当前状态：`active` / `waiting` / `incompatible` / `inactive`。
卸载或停用兼容插件会还原内存中的官方方法，建议随后重启 AstrBot。
重排序配置独立保留；如需完整回滚，恢复部署前的配置备份。

修复不自动恢复历史失败总结或已删除记忆，不包含任何特定群友的名字或资料规则。

## 来源与许可证

本插件包含基于 [LivingMemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)
2.7.0-beta.2 的方法实现及修改版本，保留上游作者的权利，按随附的 AGPL-3.0 许可证发布。
`overrides.json` 包含原始方法和修复后的完整方法源代码；其余源代码也随插件一并提供。

## 同步记录

2026-10-08：首次上传本地开发版本；尚未与 NAS 正在运行的 Docker 插件目录逐文件核对。仓库不包含运行配置、聊天数据或密钥。

回归测试依赖同级目录 `astrbot-livingmemory-fix/original` 中的 LivingMemory 2.7.0-beta.2 源码及其依赖；该外部测试夹具不随本仓库打包。
