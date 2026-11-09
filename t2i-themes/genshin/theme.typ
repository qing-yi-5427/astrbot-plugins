// Adventure-journal theme inspired by Genshin Impact; original ornamental art.
#let canvas = rgb("#d7cfbe")
#let paper = rgb("#f9f4e6")
#let soft = rgb("#f0e8d5")
#let ink = rgb("#3b4350")
#let inksoft = rgb("#66645f")
#let muted = rgb("#766e60")
#let linecolor = rgb("#d9c9aa")
#let accent = rgb("#c3a56b")
#let accentsoft = rgb("#f1e6cb")
#let accentdeep = rgb("#806234")
#let asidebg = rgb("#32434e")
#let asideink = rgb("#f6f0dd")
#let asideaccent = rgb("#e2cc91")
#let asidelabel = rgb("#a7d6c6")
#let quotebg = rgb("#edf0e3")
#let quoteink = rgb("#49625b")
#let quoteline = rgb("#8ba595")
#let codebg = rgb("#303c47")
#let codehead = rgb("#465360")
#let codeink = rgb("#f0eadc")
#let tableheadbg = rgb("#e9dfc8")
#let brand = {{BRAND}}
#let subtitle = {{SUBTITLE}}
#let version = {{VERSION}}
#let footer = {{FOOTER}}
#let av = {{SHOW_AVATAR}}
#let ft = {{SHOW_FOOTER}}
#let name-size = if brand.len() > 28 { 15pt } else if brand.len() > 14 { 18pt } else { 23pt }
#let compass = bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 300 300'><g fill='none' stroke='#bfa670'><circle cx='150' cy='150' r='113' opacity='.32'/><circle cx='150' cy='150' r='98' stroke-dasharray='2 10' opacity='.55'/><path d='M150 14L168 126L286 150L168 173L150 286L126 173L14 150L126 126Z' fill='#d9c397' fill-opacity='.16' stroke-width='1.2'/><path d='M150 51L162 138L249 150L162 161L150 249L138 161L51 150L138 138Z' opacity='.6'/><path d='M67 67L114 142L67 233L142 186L233 233L186 158L233 67L158 114Z' opacity='.18'/><circle cx='150' cy='150' r='40' opacity='.6'/></g></svg>")
#let crest = bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><g fill='none' stroke='#b89a5f' stroke-width='2'><path d='M50 4L59 37L92 50L59 62L50 96L38 62L6 50L38 37Z'/><path d='M50 19L55 42L79 50L55 57L50 81L43 57L20 50L43 42Z' fill='#d9c28a' fill-opacity='.4'/><circle cx='50' cy='50' r='43' stroke-width='.8'/></g></svg>")
#let corners = bytes("<svg xmlns='http://www.w3.org/2000/svg' width='640' height='280' viewBox='0 0 640 280'><g fill='none' stroke='#bda575' stroke-width='1.4'><path d='M12 67V17H64M20 52V25H48M576 17H628V67M592 25H620V52M12 213V263H64M20 228V255H48M576 263H628V213M592 255H620V228'/><path d='M72 17H254L270 9L286 17H354L370 9L386 17H568' opacity='.6'/><path d='M19 91V186M621 91V186' opacity='.4'/></g><g fill='#bda575'><path d='M12 73l4 7-4 7-4-7zM628 193l4 7-4 7-4-7z'/></g></svg>")
#set page(width: 360pt, height: auto, margin: (x: 14pt, y: 17pt), fill: canvas)
#set text(font: ("Source Han Sans SC", "Noto Sans CJK SC", "Noto Color Emoji"), size: 12pt, weight: "regular", fill: ink, lang: "zh")
#set par(leading: 0.78em, spacing: 0pt, first-line-indent: 0pt)
#set block(spacing: 0pt)
#show table: set text(font: ("Source Han Sans SC", "Noto Color Emoji"))
#block(width: 100%, fill: paper, radius: 5pt, stroke: 0.8pt + rgb("#bca47b"))[
  #block(width: 100%, fill: gradient.linear(rgb("#f7f0db"), rgb("#ece4d1"), angle: 0deg), inset: (x: 19pt, top: 10pt, bottom: 11pt))[
    #place(top + right, dx: 10pt, dy: -6pt)[#image(compass, format: "svg", width: 112pt, height: 112pt)]
    #place(top + left)[#image(corners, format: "svg", width: 100%, height: 93pt, fit: "stretch")]
    #grid(columns: (13pt, 1fr, auto), column-gutter: 5pt, align: horizon,
      [#image(crest, format: "svg", width: 13pt)],
      [#text("TEYVAT / 旅途手记", size: 7pt, weight: "bold", fill: accentdeep, tracking: 0.06em)],
      [#text("ADVENTURE LOG", size: 6.2pt, fill: accentdeep)],
    )
    #v(8pt)
    #let nameplate = stack(dir: ttb, spacing: 8pt,
      [#text("向着星辰与深渊", size: 7pt, fill: accentdeep) <brand-eyebrow>],
      [#text(brand, size: name-size, weight: "bold", fill: rgb("#384350")) <brand-title>],
      ..if subtitle != "" { ([#text(subtitle, size: 7.4pt, fill: muted) <brand-subtitle>],) } else { () },
    )
    #if av {
      grid(columns: (1fr, 60pt), column-gutter: 13pt, align: horizon,
        nameplate,
        [#box(width: 60pt, height: 64pt)[
          #place(top + center, dy: 5pt)[#circle(radius: 27pt, stroke: 0.8pt + accent)]
          #place(top + center, dy: 8pt)[#box(width: 48pt, height: 48pt, radius: 50%, clip: true, stroke: 1.5pt + paper)[#image("avatar.webp", width: 48pt, height: 48pt, fit: "cover")]]
        ]],
      )
    } else { nameplate }
  ]
  #block(width: 100%, fill: rgb("#3c4b59"), inset: (x: 20pt, y: 5pt))[
    #grid(columns: (1fr, auto), align: horizon,
      [#text("QUEST / 愿风指引你的道路", size: 7.3pt, fill: rgb("#eadcbc"))],
      [#text(version, size: 6.7pt, fill: rgb("#c4c5bd"))],
    )
  ]
  #block(width: 100%, inset: (x: 20pt, top: 16pt, bottom: 20pt))[
{{CONTENT}}
  ]
  #if ft [
    #block(width: 100%, inset: (x: 20pt, bottom: 14pt))[
      #grid(columns: (1fr, 14pt, 1fr), column-gutter: 8pt, align: horizon,
        [#block(width: 100%, height: 0.5pt, fill: linecolor)],
        [#image(crest, format: "svg", width: 14pt)],
        [#block(width: 100%, height: 0.5pt, fill: linecolor)],
      )
      #v(7pt)
      #align(center)[#text(footer, size: 8pt, fill: muted)]
    ]
  ]
]
