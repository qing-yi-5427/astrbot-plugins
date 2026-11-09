// Virtual singer-inspired personal theme; not an official product UI.
#let canvas = rgb("#dff5f2")
#let paper = rgb("#fbfffe")
#let soft = rgb("#eaf7f5")
#let ink = rgb("#193b3d")
#let inksoft = rgb("#45696b")
#let muted = rgb("#55777a")
#let linecolor = rgb("#c4e3df")
#let accent = rgb("#39cbbb")
#let accentsoft = rgb("#def7f1")
#let accentdeep = rgb("#117a74")
#let asidebg = rgb("#273e4c")
#let asideink = rgb("#effffb")
#let asideaccent = rgb("#74efdc")
#let asidelabel = rgb("#f6b4d2")
#let quotebg = rgb("#e7f5f3")
#let quoteink = rgb("#2e625e")
#let quoteline = rgb("#33b5a5")
#let codebg = rgb("#202e39")
#let codehead = rgb("#314653")
#let codeink = rgb("#e4fff7")
#let tableheadbg = rgb("#d7eee9")
#let brand = {{BRAND}}
#let subtitle = {{SUBTITLE}}
#let version = {{VERSION}}
#let footer = {{FOOTER}}
#let av = {{SHOW_AVATAR}}
#let ft = {{SHOW_FOOTER}}
#let name-size = if brand.len() > 28 { 14pt } else if brand.len() > 14 { 18pt } else { 22pt }
#let score = bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 650 230'><g fill='none' stroke='#278c86' opacity='.16'><path d='M0 55H650M0 74H650M0 93H650M0 112H650M0 131H650'/><circle cx='558' cy='104' r='73' stroke-width='16'/><circle cx='558' cy='104' r='48' stroke-width='1.5'/><path d='M14 175L61 128H138L168 98H249M369 0V26L399 56H463' stroke-width='2'/></g><g fill='#22aa9c' opacity='.23'><path d='M426 34v60c-18-6-30 8-21 15 10 8 31-1 31-14V52l34-10v39c-19-5-30 8-20 15 10 7 29-2 29-14V21z'/></g><g fill='#d876a4' opacity='.75'><circle cx='84' cy='174' r='3'/><rect x='580' y='185' width='35' height='3'/><rect x='580' y='191' width='17' height='3'/></g></svg>")
#let play = bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'><circle cx='20' cy='20' r='18' fill='#233b43'/><path d='M16 11l14 9-14 9z' fill='#70e3cc'/></svg>")
#set page(width: 360pt, height: auto, margin: (x: 14pt, y: 16pt), fill: canvas)
#set text(font: ("Source Han Sans SC", "Noto Color Emoji"), size: 12pt, weight: "regular", fill: ink, lang: "zh")
#set par(leading: 0.72em, spacing: 0pt, first-line-indent: 0pt)
#set block(spacing: 0pt)
#block(width: 100%, fill: paper, stroke: 0.7pt + linecolor, radius: 10pt)[
  #block(width: 100%, height: 4pt, fill: gradient.linear(rgb("#2fc5b7"), rgb("#98eed7"), angle: 0deg), radius: (top: 10pt))
  #block(width: 100%, fill: rgb("#e6f8f1"), inset: (x: 17pt, top: 8pt, bottom: 9pt))[
    #place(top + left)[#image(score, format: "svg", width: 100%, height: 82pt, fit: "cover")]
    #grid(columns: (1fr, auto), align: horizon,
      [#text("MIKU / VIRTUAL SINGER", size: 7pt, weight: "bold", fill: accentdeep, tracking: 0.08em)],
      [#box(fill: rgb("#d96096"), radius: 2pt, inset: (x: 5pt, y: 1pt))[#text("39", size: 7pt, weight: "bold", fill: white)]],
    )
    #v(8pt)
    #let nameplate = stack(dir: ttb, spacing: 6pt,
      [#text("初音ミク / 未来の音", size: 7pt, weight: "bold", fill: accentdeep)],
      [#text(brand, size: name-size, weight: "bold", fill: rgb("#183b40"))],
      ..if subtitle != "" { ([#text(subtitle, size: 7.3pt, fill: inksoft)],) } else { () },
    )
    #if av {
      grid(columns: (64pt, 1fr), column-gutter: 12pt, align: horizon,
        [#box(width: 64pt, height: 64pt)[
          #place(top + left, dx: 4pt, dy: 3pt)[#box(width: 56pt, height: 58pt, fill: white, radius: 6pt, clip: true, stroke: 1.5pt + white)[#image("avatar.webp", width: 56pt, height: 58pt, fit: "cover")]]
          #place(top + left, dy: 10pt)[#box(width: 3pt, height: 22pt, fill: rgb("#d96096"))]
          #place(bottom + right, dy: -6pt)[#box(width: 3pt, height: 22pt, fill: accent)]
        ]], nameplate,
      )
    } else { nameplate }
  ]
  // A music-player divider, not functional playback controls.
  #block(width: 100%, fill: rgb("#263c44"), inset: (x: 18pt, y: 5pt))[
    #grid(columns: (12pt, 1fr, auto), column-gutter: 8pt, align: horizon,
      [#image(play, format: "svg", width: 12pt)],
      [#grid(columns: (1fr, auto), column-gutter: 7pt, align: horizon,
        [#block(width: 100%, height: 2.4pt, fill: rgb("#507477"))[#place(top + left)[#block(width: 39%, height: 2.4pt, fill: accent)]]],
        [#text("SOUND / NOTE", size: 6.5pt, fill: rgb("#b7ded8"))])],
      [#text(version, size: 6.7pt, fill: rgb("#b7ded8"))],
    )
  ]
  #block(width: 100%, inset: (x: 20pt, top: 15pt, bottom: 19pt))[
{{CONTENT}}
  ]
  #if ft [
    #block(width: 100%, fill: soft, radius: (bottom: 10pt), inset: (x: 18pt, y: 11pt))[
      #grid(columns: (1fr, auto), align: horizon,
        [#text(footer, size: 8pt, fill: muted)],
        [#grid(columns: (2pt, 2pt, 2pt, 2pt, 2pt), column-gutter: 2pt, align: bottom,
          [#box(width: 2pt, height: 4pt, fill: accentdeep)], [#box(width: 2pt, height: 9pt, fill: accent)],
          [#box(width: 2pt, height: 6pt, fill: accentdeep)], [#box(width: 2pt, height: 12pt, fill: accent)],
          [#box(width: 2pt, height: 5pt, fill: rgb("#d96096"))])],
      )
    ]
  ]
]
