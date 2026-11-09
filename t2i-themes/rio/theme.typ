// Tsukatsuki Rio-inspired academic systems dossier, not official Blue Archive UI.
#let canvas = rgb("#dfe5ee")
#let paper = rgb("#fbfcfe")
#let soft = rgb("#edf1f6")
#let ink = rgb("#29364b")
#let inksoft = rgb("#53647d")
#let muted = rgb("#62728a")
#let linecolor = rgb("#ced7e3")
#let accent = rgb("#d85464")
#let accentsoft = rgb("#faedf0")
#let accentdeep = rgb("#a33e51")
#let asidebg = rgb("#2d3345")
#let asideink = rgb("#f7eff4")
#let asideaccent = rgb("#efb3bc")
#let asidelabel = rgb("#c0d3f0")
#let quotebg = rgb("#edf2f9")
#let quoteink = rgb("#4b6080")
#let quoteline = rgb("#89a0c4")
#let codebg = rgb("#202735")
#let codehead = rgb("#354157")
#let codeink = rgb("#edf0fa")
#let tableheadbg = rgb("#e2eaf4")
#let brand = {{BRAND}}
#let subtitle = {{SUBTITLE}}
#let version = {{VERSION}}
#let footer = {{FOOTER}}
#let av = {{SHOW_AVATAR}}
#let ft = {{SHOW_FOOTER}}
#let name-size = if brand.len() > 28 { 14pt } else if brand.len() > 14 { 18pt } else { 22pt }
#let schematic = bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 650 290'><g fill='none' stroke='#7590b5' opacity='.18' stroke-width='1'><path d='M13 40H637M13 80H637M13 120H637M13 160H637M13 200H637M13 240H637M40 8V281M80 8V281M120 8V281M160 8V281M200 8V281M240 8V281M280 8V281M320 8V281M360 8V281M400 8V281M440 8V281M480 8V281M520 8V281M560 8V281M600 8V281'/><circle cx='532' cy='151' r='88'/><circle cx='532' cy='151' r='66'/><path d='M444 151H620M532 63V239'/><path d='M28 246H192L224 214H344'/></g><g fill='none' stroke='#c85d6d' opacity='.36' stroke-width='2'><path d='M455 79L503 53L561 64L604 106M451 210L493 234L564 227'/><path d='M19 41V18H66M584 264H630V217'/></g></svg>")
#let halo = bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 240 56'><g fill='none'><ellipse cx='120' cy='29' rx='100' ry='18' stroke='#3d424d' stroke-width='8'/><path d='M30 25L65 15L88 21L114 11L143 21L168 13L198 25M40 39L68 30L92 38L116 28L145 39L172 30L198 37' stroke='#d7596b' stroke-width='6'/><path d='M17 21H41M199 37H222' stroke='#252b35' stroke-width='4'/></g></svg>")
#let monogram = bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 70 55'><g fill='none' stroke='#516781' stroke-width='4'><path d='M5 45L19 12L35 35L51 12L65 45M17 45L27 24M43 24L53 45'/></g><path d='M8 50H62' stroke='#cf6570' stroke-width='3'/></svg>")
#set page(width: 360pt, height: auto, margin: (x: 14pt, y: 17pt), fill: canvas)
#set text(font: ("Source Han Sans SC", "Noto Color Emoji"), size: 12pt, weight: "regular", fill: ink, lang: "zh")
#set par(leading: 0.74em, spacing: 0pt, first-line-indent: 0pt)
#set block(spacing: 0pt)
#block(width: 100%, fill: paper, radius: 4pt, stroke: 0.7pt + rgb("#becbdc"))[
  #block(width: 100%, fill: rgb("#eef3fa"), radius: (top: 4pt), inset: (x: 17pt, top: 9pt, bottom: 11pt))[
    #place(top + left)[#image(schematic, format: "svg", width: 100%, height: 93pt, fit: "cover")]
    #grid(columns: (14pt, 1fr, auto), column-gutter: 6pt, align: horizon,
      [#image(monogram, format: "svg", width: 14pt)],
      [#text("MILLENNIUM / SEMINAR", size: 7.2pt, weight: "bold", fill: rgb("#536c89"), tracking: 0.07em)],
      [#box(fill: rgb("#263549"), inset: (x: 5pt, y: 1pt), radius: 2pt)[#text("RIO / 01", size: 6.5pt, fill: white, weight: "bold")]],
    )
    #v(9pt)
    #let identity = stack(dir: ttb, spacing: 6pt,
      [#text("調月リオ / 理性的观测者", size: 7pt, fill: accentdeep, weight: "bold")],
      [#text(brand, size: name-size, weight: "bold", fill: rgb("#29394e"))],
      ..if subtitle != "" { ([#text(subtitle, size: 7.3pt, fill: inksoft)],) } else { () },
    )
    #if av {
      grid(columns: (1fr, 66pt), column-gutter: 12pt, align: horizon,
        identity,
        [#box(width: 66pt, height: 70pt)[
          #place(top + center, dy: 2pt)[#box(width: 62pt, height: 64pt, clip: true, radius: 3pt, stroke: 0.8pt + rgb("#aebed3"))[#image("avatar.webp", width: 62pt, height: 64pt, fit: "cover")]]
          #place(bottom + right)[#box(width: 18pt, height: 2pt, fill: accent)]
        ]],
      )
    } else { identity }
  ]
  #grid(columns: (31pt, 1fr, 24pt), column-gutter: 0pt,
    [#block(width: 100%, height: 2pt, fill: accent)],
    [#block(width: 100%, height: 2pt, fill: rgb("#b3c4dc"))],
    [#block(width: 100%, height: 2pt, fill: rgb("#2c3b51"))],
  )
  #block(width: 100%, fill: rgb("#e7edf5"), inset: (x: 20pt, y: 5pt))[
    #grid(columns: (1fr, auto), align: horizon,
      [#text("SYSTEM ARCHIVE / 思考记录", size: 7pt, weight: "bold", fill: rgb("#526b8a"))],
      [#text(version, size: 6.7pt, fill: muted)],
    )
  ]
  #block(width: 100%, inset: (x: 20pt, top: 15pt, bottom: 20pt))[
{{CONTENT}}
  ]
  #if ft [
    #block(width: 100%, fill: soft, radius: (bottom: 4pt), inset: (x: 18pt, y: 11pt))[
      #grid(columns: (3pt, 1fr, auto), column-gutter: 7pt, align: horizon,
        [#box(width: 3pt, height: 11pt, fill: accent)],
        [#text(footer, size: 8pt, fill: muted)],
        [#text("LOG / 01", size: 6pt, fill: rgb("#7e8ca1"))],
      )
    ]
  ]
]
