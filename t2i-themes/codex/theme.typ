#let canvas = rgb("#eef2f8")
#let paper = rgb("#fafdff")
#let soft = rgb("#f2f6fc")
#let ink = rgb("#203045")
#let inksoft = rgb("#70829b")
#let muted = rgb("#70829b")
#let linecolor = rgb("#d9e3ef")
#let accent = rgb("#6dc8ff")
#let accentsoft = rgb("#e9f3ff")
#let accentdeep = rgb("#28658e")
#let asidebg = rgb("#142e46")
#let asideink = rgb("#eef6ff")
#let asideaccent = rgb("#80cfff")
#let asidelabel = rgb("#e6c58d")
#let quotebg = rgb("#edf8f2")
#let quoteink = rgb("#2c5140")
#let quoteline = rgb("#65a88a")
#let codebg = rgb("#25262e")
#let codehead = rgb("#333541")
#let codeink = rgb("#edf0fb")
#let tableheadbg = rgb("#e1edf9")
#set page(width: 360pt, height: auto, margin: (x: 14pt, y: 17pt), fill: canvas)
#set text(font: ("Source Han Sans SC", "Noto Sans CJK SC", "Noto Color Emoji"), size: 12pt, fill: ink)
#set par(leading: 0.65em, spacing: 0pt)
#let identity = stack(dir: ttb, spacing: 4pt,
  [#text({{BRAND}}, size: 13.5pt, weight: "bold", fill: ink)],
  ..if {{SUBTITLE}} != "" { ([#text({{SUBTITLE}}, size: 8pt, fill: inksoft)],) } else { () },
)
#block(width: 100%, fill: paper, radius: 8pt, inset: 0pt, stroke: 0.5pt + linecolor)[
  #block(width: 100%, height: 2pt, fill: accent)
  #block(width: 100%, inset: (x: 19pt, y: 7pt))[
    #grid(columns: (1fr, auto), column-gutter: 8pt, align: horizon,
      [#if {{SHOW_AVATAR}} {
        grid(columns: (26pt, 1fr), column-gutter: 8pt, align: horizon,
          [#box(width: 26pt, height: 26pt, radius: 50%, clip: true, stroke: 1.3pt + accent)[#image("avatar.webp", width: 26pt, height: 26pt, fit: "cover")]], identity)
      } else { identity }],
      [#box(fill: accentsoft, radius: 5pt, inset: (x: 6pt, y: 3pt))[#text({{VERSION}}, size: 7.2pt, weight: "bold", fill: accentdeep)]]
    )
  ]
  #block(width: 100%, height: 0.5pt, fill: linecolor)
  #block(width: 100%, inset: (x: 21pt, top: 19pt, bottom: 22pt))[
{{CONTENT}}
  ]
  #if {{SHOW_FOOTER}} [
    #block(width: 100%, height: 0.5pt, fill: linecolor)
    #block(width: 100%, fill: soft, inset: (x: 17pt, y: 12pt))[
      #align(center)[#text({{FOOTER}}, size: 8.5pt, fill: muted)]
    ]
  ]
]
