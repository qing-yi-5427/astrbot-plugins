// Astral railway ticket-inspired theme. Original vector ornaments, not official UI.
#let canvas = rgb("#dce1ed")
#let paper = rgb("#faf9f5")
#let soft = rgb("#f0eef4")
#let ink = rgb("#293448")
#let inksoft = rgb("#5a6276")
#let muted = rgb("#6c6e80")
#let linecolor = rgb("#d7d3dd")
#let accent = rgb("#d1b67a")
#let accentsoft = rgb("#f4ebd6")
#let accentdeep = rgb("#806537")
#let asidebg = rgb("#353247")
#let asideink = rgb("#f6f0fc")
#let asideaccent = rgb("#e4c999")
#let asidelabel = rgb("#c6bdf1")
#let quotebg = rgb("#edf0f8")
#let quoteink = rgb("#4e5c7b")
#let quoteline = rgb("#8799be")
#let codebg = rgb("#1d2638")
#let codehead = rgb("#35415a")
#let codeink = rgb("#edf1fc")
#let tableheadbg = rgb("#e8e6ee")
#let brand = {{BRAND}}
#let subtitle = {{SUBTITLE}}
#let version = {{VERSION}}
#let footer = {{FOOTER}}
#let av = {{SHOW_AVATAR}}
#let ft = {{SHOW_FOOTER}}
#let name-size = if brand.len() > 28 { 14pt } else if brand.len() > 14 { 17pt } else { 21pt }
#let cosmos = bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 650 260'><g fill='none' stroke='#c9b27d'><ellipse cx='522' cy='124' rx='136' ry='74' transform='rotate(-23 522 124)' opacity='.42'/><ellipse cx='522' cy='124' rx='108' ry='96' transform='rotate(32 522 124)' opacity='.18'/><path d='M65 211L175 184L273 205L368 155L496 169L619 86' opacity='.28'/></g><g fill='#ddd1b2'><path d='M521 64l9 43 42 12-42 12-9 43-11-43-42-12 42-12z' opacity='.45'/><circle cx='65' cy='211' r='2'/><circle cx='175' cy='184' r='2'/><circle cx='273' cy='205' r='2'/><circle cx='368' cy='155' r='2'/><circle cx='496' cy='169' r='2'/><circle cx='619' cy='86' r='2'/><circle cx='71' cy='44' r='1.5'/><circle cx='202' cy='72' r='1'/><circle cx='369' cy='35' r='2'/><circle cx='601' cy='32' r='1.5'/></g><path d='M402 254L646 10' stroke='#a5b0d8' opacity='.16' stroke-width='24'/></svg>")
#let emblem = bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 80 80'><g fill='none' stroke='#e1c891' stroke-width='2'><path d='M14 58V26L25 16H55L66 26V58Z'/><path d='M21 33H59V48H21ZM26 59L20 70M54 59L60 70M21 65H59'/><path d='M32 8L40 1L48 8'/><circle cx='27' cy='55' r='2' fill='#e1c891'/><circle cx='53' cy='55' r='2' fill='#e1c891'/></g></svg>")
#let barcode = bytes("<svg xmlns='http://www.w3.org/2000/svg' width='150' height='24' viewBox='0 0 150 24'><g fill='#8c8399'><path d='M0 0h2v24H0zM5 0h1v24H5zM9 0h4v24H9zM16 0h2v24H16zM21 0h1v24H21zM25 0h5v24H25zM33 0h2v24H33zM39 0h3v24H39zM46 0h1v24H46zM50 0h3v24H50zM56 0h2v24H56zM62 0h5v24H62zM71 0h1v24H71zM76 0h3v24H76zM83 0h2v24H83zM89 0h1v24H89zM94 0h4v24H94zM102 0h2v24H102zM108 0h5v24H108zM117 0h1v24H117zM122 0h3v24H122zM129 0h2v24H129zM135 0h4v24H135zM143 0h1v24H143zM147 0h3v24H147z'/></g></svg>")
#set page(width: 360pt, height: auto, margin: (x: 14pt, y: 17pt), fill: canvas)
#set text(font: ("Source Han Sans SC", "Noto Color Emoji"), size: 12pt, weight: "regular", fill: ink, lang: "zh")
#set par(leading: 0.74em, spacing: 0pt, first-line-indent: 0pt)
#set block(spacing: 0pt)
#block(width: 100%, fill: paper, radius: 8pt, stroke: 0.6pt + rgb("#bfc4d0"))[
  #block(width: 100%, fill: gradient.linear(rgb("#1c2743"), rgb("#3b3353"), angle: 15deg), radius: (top: 8pt), inset: (x: 17pt, top: 9pt, bottom: 11pt))[
    #place(top + left)[#image(cosmos, format: "svg", width: 100%, height: 84pt, fit: "cover")]
    #grid(columns: (13pt, 1fr, auto), column-gutter: 6pt, align: horizon,
      [#image(emblem, format: "svg", width: 13pt)],
      [#text("ASTRAL EXPRESS", size: 7.4pt, weight: "bold", fill: rgb("#ead7ad"), tracking: 0.12em)],
      [#box(stroke: 0.45pt + rgb("#9e8b73"), inset: (x: 5pt, y: 1pt), radius: 2pt)[#text("星穹列车", size: 6.5pt, fill: rgb("#ead7ad"))]],
    )
    #v(9pt)
    #let identity = stack(dir: ttb, spacing: 6pt,
      [#text("TRAILBLAZER / 开拓者档案", size: 6.7pt, fill: rgb("#cabaa0"), weight: "bold")],
      [#text(brand, size: name-size, weight: "bold", fill: rgb("#f7f2e8"))],
      ..if subtitle != "" { ([#text(subtitle, size: 7.3pt, fill: rgb("#c0c7df"))],) } else { () },
    )
    #if av {
      grid(columns: (54pt, 1fr), column-gutter: 12pt, align: horizon,
        [#box(width: 54pt, height: 62pt)[
          #place(top + left, dx: 2pt, dy: 2pt)[#box(width: 50pt, height: 56pt, stroke: 1pt + rgb("#c5ae7c"), radius: 5pt, clip: true)[#image("avatar.webp", width: 50pt, height: 56pt, fit: "cover")]]
        ]], identity,
      )
    } else { identity }
  ]
  // Perforated ticket edge; the barcode below is decorative, not a QR/data code.
  #block(width: 100%, fill: rgb("#e9e5ec"), inset: (x: 15pt, y: 5pt))[
    #place(left + horizon, dx: -20pt)[#circle(radius: 6pt, fill: canvas)]
    #place(right + horizon, dx: 20pt)[#circle(radius: 6pt, fill: canvas)]
    #grid(columns: (1fr, auto), align: horizon,
      [#text("旅程记录 / JOURNEY NOTES", size: 7pt, weight: "bold", fill: rgb("#685b76"))],
      [#text(version, size: 6.7pt, fill: muted)],
    )
  ]
  #block(width: 100%, inset: (x: 20pt, top: 16pt, bottom: 20pt))[
{{CONTENT}}
  ]
  #if ft [
    #block(width: 100%, fill: soft, radius: (bottom: 8pt), inset: (x: 18pt, y: 11pt))[
      #grid(columns: (1fr, 57pt), column-gutter: 8pt, align: horizon,
        [#text(footer, size: 8pt, fill: muted)],
        [#image(barcode, format: "svg", width: 57pt, height: 10pt)],
      )
    ]
  ]
]
