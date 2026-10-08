# Tavily / AnySearch 结果限制器

## 仓库安装

在 AstrBot 插件管理中通过仓库地址安装：

```text
https://github.com/qing-yi-5427/tavily_result_limiter
```

插件元数据和入口位于仓库根目录；更新可通过 AstrBot 插件管理完成。

Tavily / AnySearch 结果限制器 1.2.0

保留插件名 tavily_result_limiter，升级后沿用已有配置。
max_results：默认 10；Tavily 上限 20，AnySearch 上限 10；模型要求更少时按较少值返回。
每条搜索摘要保留前 1000 字符并添加截断提示，保留标题、网址和引用索引。
Tavily 网页正文保留前 6000 字符并添加截断提示。当前 AstrBot 的 AnySearch 没有独立网页提取工具。
auto_clear_history：默认开启；新 LLM 请求前将历史 Tavily / AnySearch 工具结果替换为短占位文本，保留工具调用配对及其他对话内容。
AnySearch 的 tag、zone、language、params 参数原样交给 AstrBot，垂直搜索摘要也受长度限制。
插件仅适配 AstrBot 内置工具，不覆盖同名服务的第三方插件或 MCP 工具。

不修改 AstrBot 核心代码。停用或卸载后恢复原工具方法。
测试：在当前 AstrBot 容器内运行 tests/test_limiter.py；8 项测试覆盖实际工具类、参数、结果限制、历史清理、重载和旧版本兼容，搜索网络调用使用模拟数据。

## 同步记录

2026-10-08：已与 NAS 正在运行的 AstrBot Docker 插件目录逐文件核对，插件代码及配置结构一致。仓库不包含运行配置、聊天数据或密钥。
