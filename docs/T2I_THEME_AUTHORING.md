# 给其他 agent 的 T2I 主题编写说明

你要为 AstrBot 的本地轻量文转图服务创作一个 **Typst 主题**。在这个仓库的 `t2i-themes/<主题ID>/` 中交付文件即可，不需要修改 Python 插件或 Docker 服务。

**以能正确渲染为验收标准。现有六套只是参考，不是必须照抄的格式。** 可以重新设计布局、配色、头像形状、标题位置、页头高度、背景和装饰。不要为了符合某套现有模板，牺牲主题自己的设计。

## 最小交付

唯一必需的文件是 `t2i-themes/my-theme/theme.typ`。`my-theme` 是指令使用的 ID：小写字母开头，可含数字、`-`、`_`，最长 48 字符；不限制显示的中文名称。

`theme.typ` 是普通 UTF-8 Typst 源码，只需至少包含一个正文插入点：

```typst
{{CONTENT}}
```

服务会把回复的 Markdown 转成 Typst，再放到这里。它不是需要再次转义、再次用 `text()` 包起来的纯文本，直接放在内容区即可。建议只放一次，重复放置会重复全文。

上面这一行本身就是一个可渲染的最小主题：服务会提供默认的页面、字体和 Markdown 配色变量。你可以按需覆盖它们。

## 可直接改造的例子

```typst
#let paper = rgb("#fffaf1")
#let ink = rgb("#273548")
#let accentdeep = rgb("#94623e")
#set page(width: 360pt, height: auto,
  margin: (x: 16pt, y: 14pt), fill: paper)
#set text(font: ("Source Han Sans SC", "Noto Color Emoji"),
  size: 12pt, fill: ink)
#set par(leading: 0.7em, spacing: 0pt)

#block(width: 100%, inset: (x: 12pt, y: 10pt),
  fill: rgb("#f0e5d4"), radius: 8pt)[
  #grid(columns: (1fr, auto), column-gutter: 12pt, align: horizon,
    [#stack(dir: ttb, spacing: 5pt,
      [#text(brand, size: 18pt, weight: "bold")],
      [#text(subtitle, size: 8pt)],
    )],
    [#if av [
      #box(width: 32pt, height: 32pt, radius: 50%, clip: true)[
        #image("avatar.webp", width: 32pt, height: 32pt, fit: "cover")
      ]
    ]],
  )
]
#v(12pt)
{{CONTENT}}
#if ft [
  #v(12pt)
  #text(footer, size: 8pt, fill: rgb("#77818f"))
]
```

结构、字号、宽度、颜色、是否显示头像等都可以改。这里只演示正文和页面元数据如何接入。

## 可选文件

```text
t2i-themes/my-theme/
  theme.typ              # 必需，主模板
  theme.json             # 可选，菜单名称、默认品牌等
  avatar.webp            # 可选；也可以是 avatar.png / avatar.jpg
  assets/                # 可选，局部 Typst、SVG、图片或其他数据
    ornament.svg
    header.typ
  README.md              # 可选，说明与素材来源，不参与渲染
  preview.png            # 可选，供人查看，不作为渲染资源下载
```

没有 `theme.json` 也可以使用。新 ID 默认用 ID 作为名称与品牌；覆盖现有 ID 时沿用该内置主题的默认品牌信息。

`theme.json` 示例，字段均可省略，额外说明字段会被忽略：

```json
{
  "name": "我的新主题",
  "brand_name": "机器人名称",
  "subtitle": "可选副标题",
  "description": "一句简短说明"
}
```

`name`、`brand_name` 最长 64 字，`subtitle` 最长 96 字，`description` 最长 256 字。它们是显示数据，不是 Typst 代码。

头像会转换为统一的 `avatar.webp` 提供给模板。没有默认头像且管理员也未指定自定义头像时，`av` 为 `false`。希望开关有效时，用 `av` 决定是否画头像；不要在没有资源时无条件引用头像文件。

可以使用主题内的相对资源路径：

```typst
#import "assets/header.typ": draw-header
#image("assets/ornament.svg", width: 40pt)
```

需要的局部模块、图片和数据放入 `assets/`。资源只在当前主题的临时目录内可见，不要引用 NAS 绝对路径、其他主题或机器上的私有文件，也不要依赖渲染时临时下载。

## 服务提供的变量

这些变量已定义，可以直接用，也可以覆盖。它们来自当前回复或管理员配置，不应写死为某个测试值。

| 变量 | 含义 |
|---|---|
| `brand` | 当前主题默认或管理员自定义的机器人名称 |
| `subtitle` | 当前副标题，可能为空 |
| `version` | AstrBot 版本 |
| `footer` | 本次真实回复模型说明；未知时有明确标记 |
| `av` | 是否显示头像，布尔值 |
| `ft` | 是否显示模型页脚，布尔值 |

也兼容现有模板的占位符：`{{BRAND}}`、`{{SUBTITLE}}`、`{{VERSION}}`、`{{FOOTER}}`、`{{SHOW_AVATAR}}`、`{{SHOW_FOOTER}}`。字符串占位符已带转义与引号，写 `#text({{BRAND}})`，不要再给它加一层引号。正文入口是 `{{CONTENT}}`。

建议用 `footer`，不要写固定模型名或让主题猜当前模型。主题负责图片呈现，不切换人格、不选择模型。

## Markdown 的默认配色

服务提供以下变量，不需要把它们全部重写。想改变某类 Markdown 的颜色时，只覆盖相关变量即可。

| 用途 | 变量 |
|---|---|
| 页面与普通文字 | `canvas`、`paper`、`soft`、`ink`、`inksoft`、`muted`、`linecolor` |
| 强调与链接 | `accent`、`accentsoft`、`accentdeep` |
| 开头个人旁白 | `asidebg`、`asideink`、`asideaccent`、`asidelabel` |
| 引用块 | `quotebg`、`quoteink`、`quoteline` |
| 代码块 | `codebg`、`codehead`、`codeink` |
| 表格表头 | `tableheadbg` |

字体、颜色也可用 `#set text(...)` 设置。容器可用思源黑体 `Source Han Sans SC` 常规/粗体、Noto Sans SC、Noto Sans CJK SC、Noto Serif SC、Noto Color Emoji、DejaVu Sans Mono。默认思源黑体符合当前用户阅读偏好，设计仍可按主题需要调整。

## 渲染环境和设计建议

- 引擎是 Typst 0.15.1 与 Pillow，不是浏览器；不要交付 HTML/CSS 或需要浏览器执行的脚本。
- 当前参考宽度 720px，对应 `360pt` 在 144ppi 下输出；正文常用 12pt。宽度、字号、页头高度都是建议，不是强制格式。
- 一个回复输出一张可变高度图片，建议 `height: auto`；避免固定 A4 页导致分页。
- 常用 Markdown 已由服务处理：标题、段落、粗体、斜体、删除线、链接、引用、嵌套列表、任务框、行内/块代码、表格、分隔线、Emoji、图片说明。
- 关注长中文名称、英文副标题、替换头像、关闭头像/页脚和长代码行；不要只拿两行短文调试。
- 不同字号的标题用 `stack` 或明确块间距，避免只靠换行导致大小字互相挤压。
- 限定装饰图的绘制范围，避免 `place` 越过页头压住正文。现有紧凑页头可参考，不需要保持相同高度。
- 用户偏好不在图片外加独立素材署名白条，素材说明写进 README。现有默认头像为项目重新生成的角色插画。

以下是传输和运行边界，不是视觉格式：主源码不超过 256KiB；单主题资源合计不超过 2MiB、最多 64 个文件；头像不超过 512000 字节和 1024×1024。请求上限 4MiB、编译超时 25 秒、图片最高 25000px。请考虑轻量部署。

## 验证与交付

如能访问支持 `theme_protocol: 1` 的本地服务，运行：

```sh
python3 scripts/preview_t2i_theme.py t2i-themes/my-theme \
  --service http://localhost:8000/text2img --output my-theme-preview.png
```

脚本用内置 Markdown 样例，不调用 LLM、不向群里发消息。请人工查看正文、标题、引用、代码、表格、任务框和 Emoji；HTTP 200 不等于美观或可读。

无法访问服务时可先交付源码，明确说明没有实际试渲染，不要编造测试结果。用户推送后，插件同步时会做实际试渲染，失败不会替换现有主题。

交付完整主题文件夹，附简短说明：主题 ID、视觉方向、使用的资源、已完成验证。无需提交插件代码、Docker 文件、模型/人格配置、缓存或凭据。

## 用户上传后的流程

1. 把完整文件夹上传到仓库 `t2i-themes/`，提交到配置分支。无需改索引或 Python 名单。
2. 管理员 `/t2i sync` 立即同步，或等待后台检查。
3. `/t2i list` 查看新 ID；`/t2i use my-theme` 切当前会话，`/t2i global use my-theme` 改全局默认。
4. 更新同一个 ID 的文件，会在同步成功后替换该主题，不需要重新选择或重建 Docker。
5. 下载或试渲染失败继续使用上个可用版本。`/t2i rollback` 切回上个已验证版本；回退后等仓库出现新 commit 再自动前进。

`t2i-themes/codex`、`miku`、`genshin`、`starrail`、`zzz`、`rio` 是六套完整实例。可以参考、复制、改造，也可以从最小主题重新设计。
