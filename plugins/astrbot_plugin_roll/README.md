> 本插件已统一维护于 [astrbot-plugins](https://github.com/qing-yi-5427/astrbot-plugins)。请从该仓库 [Releases](https://github.com/qing-yi-5427/astrbot-plugins/releases) 下载本插件 ZIP 后本地上传安装，或复制当前子目录到 `data/plugins/`。多插件仓库不能直接作为单插件 Git 地址安装；更新请重新上传 ZIP。

# AstrBot 随机选择插件


## 功能特性

- 🎲 随机选择：从多个选项中随机选择一个
- 🔢 数字范围：在指定数字范围内随机选择一个整数
- 💬 简单易用：只需发送 `/roll` 命令加选项即可
- 🛡️ 错误处理：无选项时提供友好的使用提示
- ⚡ 异步处理：使用异步方式处理消息，性能优异

## 安装

### 方式一：手动安装

1. 将插件目录放置在 AstrBot 的插件目录下：
   ```
   AstrBot/data/plugins/astrbot_plugin_roll/
   ```

2. 重启 AstrBot 或在 WebUI 中重载插件

### 方式二：通过 WebUI 安装

从统一仓库 Releases 下载本插件的独立 ZIP，在 AstrBot 中本地上传安装。

## 使用方法

### 基本用法

**选项随机选择：**
```
/roll 选项1 选项2 选项3 ...
```

**数字范围随机选择：**
```
/roll a-b
```
在数字 a 和 b 之间随机选择一个整数（包含 a 和 b）

### 示例

**数字范围选择：**
```
用户: /roll 1-100
插件: 🎲 随机选择结果：42
```

```
用户: /roll 1-6
插件: 🎲 随机选择结果：3
```

**多选项选择：**
```
用户: /roll 苹果 香蕉 橙子
插件: 🎲 随机选择结果：香蕉
```

**两个选项选择：**
```
用户: /roll 吃饭 睡觉
插件: 🎲 随机选择结果：吃饭
```

**单个选项：**
```
用户: /roll 唯一的选择
插件: 🎲 随机选择结果：唯一的选择
```

**无选项提示：**
```
用户: /roll
插件: 请提供选项！用法：/roll 选项1 选项2 选项3... 或 /roll a-b
```

## 技术信息

- **插件名称**: astrbot_plugin_roll
- **版本**: v1.0.0
- **作者**: Developer
- **AstrBot 版本要求**: >=4.16.0
- **依赖**: 无第三方依赖，仅使用 Python 标准库

## 文件结构

```
astrbot_plugin_roll/
├── main.py           # 插件主逻辑
├── metadata.yaml     # 插件元数据
├── requirements.txt  # 依赖管理（空）
└── README.md         # 说明文档
```

## 开发说明

本插件基于 AstrBot 插件开发规范开发，使用以下技术：

- Python 异步编程（async/await）
- AstrBot API（@register, @filter.command 装饰器）
- Python 标准库 random 模块

## 许可证

MIT License

## 反馈与支持

如有问题或建议，欢迎提交 Issue 或 Pull Request。

## 同步记录

2026-10-08：已与 NAS 正在运行的 AstrBot Docker 插件目录核对，插件代码及配置结构一致。
