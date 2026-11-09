# 可同步的 T2I 主题

每个子目录是一套主题。`theme.typ` 必需，`theme.json`、头像和 `assets/` 可选。插件自动发现目录，不维护手写索引。

| ID | 主题 |
|---|---|
| `codex` | Amadeus / Codex 经典 |
| `miku` | 初音未来 |
| `genshin` | 原神 / 派蒙 |
| `starrail` | 星穹铁道 / 帕姆 |
| `zzz` | 绝区零 / 伊埃斯 |
| `rio` | 蔚蓝档案 / 调月莉音 |

**发给其他 agent：[主题编写说明](../docs/T2I_THEME_AUTHORING.md)**。能正常渲染即可，不强制采用现有设计。

启用插件 Git 同步后，默认读取本仓库 `main` 分支下的此目录；启动时和每 30 分钟在后台检查。`/t2i sync` 立即同步，`/t2i rollback` 回退；选择主题仍用 `/t2i use ID` 或 `/t2i global use ID`。

同步先读取同一 commit 并校验 Git blob，再在本地服务试渲染，全部通过后原子替换缓存。普通回复只读本地缓存，不访问 GitHub。插件需要 `astrbot_plugin_t2i_style` v1.2.0，服务需要 [astrbot-t2i-lite](https://github.com/qing-yi-5427/astrbot-t2i-lite) 2.1.0+。
