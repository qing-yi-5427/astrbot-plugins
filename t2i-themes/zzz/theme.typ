// New Eridu / urban VHS inspired theme. Illustrations and layout are original.
#let canvas = rgb("#23242a")
#let paper = rgb("#f8f6ee")
#let soft = rgb("#eeece3")
#let ink = rgb("#26262b")
#let inksoft = rgb("#5b5b60")
#let muted = rgb("#6a685f")
#let linecolor = rgb("#d7d3c8")
#let accent = rgb("#d9ee47")
#let accentsoft = rgb("#eff4cf")
#let accentdeep = rgb("#576715")
#let asidebg = rgb("#3a3547")
#let asideink = rgb("#f5f0ff")
#let asideaccent = rgb("#d6c6ff")
#let asidelabel = rgb("#e0ed80")
#let quotebg = rgb("#eeefdd")
#let quoteink = rgb("#4b5038")
#let quoteline = rgb("#9ca33b")
#let codebg = rgb("#25252b")
#let codehead = rgb("#3b3c43")
#let codeink = rgb("#f3f1d9")
#let tableheadbg = rgb("#e4e7c6")
#let brand = {{BRAND}}
#let subtitle = {{SUBTITLE}}
#let version = {{VERSION}}
#let footer = {{FOOTER}}
#let av = {{SHOW_AVATAR}}
#let ft = {{SHOW_FOOTER}}
#let name-size = if brand.len() > 28 { 14pt } else if brand.len() > 14 { 18pt } else { 24pt }
#let tape = bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 190 70'><rect x='2' y='2' width='186' height='66' rx='7' fill='none' stroke='#dce84b' stroke-width='3'/><rect x='31' y='17' width='128' height='35' rx='17' fill='#36383e' stroke='#626454'/><circle cx='52' cy='34' r='14' fill='none' stroke='#dce84b' stroke-width='2'/><circle cx='138' cy='34' r='14' fill='none' stroke='#dce84b' stroke-width='2'/><circle cx='52' cy='34' r='4' fill='#dce84b'/><circle cx='138' cy='34' r='4' fill='#dce84b'/><path d='M12 58H28M162 58H178' stroke='#dce84b' stroke-width='2'/></svg>")
#let checker = bytes("<svg xmlns='http://www.w3.org/2000/svg' width='200' height='16' viewBox='0 0 200 16'><g fill='#26262b'><path d='M0 0h8v8H0zM16 0h8v8h-8zM32 0h8v8h-8zM48 0h8v8h-8zM64 0h8v8h-8zM80 0h8v8h-8zM96 0h8v8h-8zM112 0h8v8h-8zM128 0h8v8h-8zM144 0h8v8h-8zM160 0h8v8h-8zM176 0h8v8h-8zM192 0h8v8h-8zM8 8h8v8H8zM24 8h8v8h-8zM40 8h8v8h-8zM56 8h8v8h-8zM72 8h8v8h-8zM88 8h8v8h-8zM104 8h8v8h-8zM120 8h8v8h-8zM136 8h8v8h-8zM152 8h8v8h-8zM168 8h8v8h-8zM184 8h8v8h-8z'/></g></svg>")
#let slashes = bytes("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 110 100'><g fill='#d9ee47'><path d='M0 100L58 0H77L19 100zM30 100L88 0H107L49 100zM60 100L110 14V47L79 100z'/></g></svg>")
#set page(width: 360pt, height: auto, margin: (x: 14pt, y: 16pt), fill: canvas)
#set text(font: ("Source Han Sans SC", "Noto Color Emoji"), size: 12pt, weight: "regular", fill: ink, lang: "zh")
#set par(leading: 0.72em, spacing: 0pt, first-line-indent: 0pt)
#set block(spacing: 0pt)
#block(width: 100%, fill: paper, radius: 5pt, stroke: 1pt + rgb("#101115"))[
  #block(width: 100%, fill: rgb("#17191f"), radius: (top: 5pt), inset: (x: 17pt, top: 7pt, bottom: 9pt))[
    #place(top + right, dx: 14pt, dy: -8pt)[#image(slashes, format: "svg", width: 48pt, height: 44pt)]
    #grid(columns: (1fr, auto), align: horizon,
      [#text("ZZZ / NEW ERIDU", size: 8pt, weight: "bold", fill: accent, tracking: 0.07em)],
      [#box(fill: rgb("#17191f"), inset: (x: 4pt, y: 1pt))[
        #grid(columns: (auto, 30pt), column-gutter: 5pt, align: horizon,
          [#text("VIDEO ARCHIVE", size: 6.2pt, fill: rgb("#b8b9ba"))],
          [#image(tape, format: "svg", width: 30pt, height: 11pt)],
        )
      ]],
    )
    #v(6pt)
    #let identity = stack(dir: ttb, spacing: 6pt,
      [#box(fill: accent, radius: 2pt, inset: (x: 5pt, y: 2pt))[#text("绳匠的录像档案", size: 6.7pt, weight: "bold", fill: rgb("#24262d"))]],
      [#text(brand, size: name-size, weight: "black", fill: rgb("#fffdf0"))],
      ..if subtitle != "" { ([#text(subtitle, size: 7.3pt, fill: rgb("#b8b9ba"))],) } else { () },
    )
    #if av {
      grid(columns: (61pt, 1fr), column-gutter: 12pt, align: horizon,
        [#box(width: 61pt, height: 61pt)[
          #place(top + left, dx: 3pt, dy: 2pt)[#box(width: 54pt, height: 54pt, fill: accent, radius: 6pt, stroke: 1.5pt + accent, clip: true)[#image("avatar.webp", width: 54pt, height: 54pt, fit: "cover")]]
          #place(bottom + right, dx: 1pt, dy: 1pt)[#rotate(-8deg)[#box(fill: rgb("#a18bd9"), inset: (x: 5pt, y: 2pt), radius: 2pt)[#text("PLAY", size: 6.5pt, weight: "black", fill: rgb("#18191e"))]]]
        ]], identity,
      )
    } else { identity }
  ]
  #block(width: 100%, fill: accent, inset: (x: 17pt, y: 5pt))[
    #grid(columns: (1fr, 56pt, auto), column-gutter: 8pt, align: horizon,
      [#text("RANDOM PLAY / 日常记录", size: 7pt, weight: "bold", fill: rgb("#282b22"))],
      [#image(checker, format: "svg", width: 56pt, height: 5pt)],
      [#text(version, size: 6.7pt, fill: rgb("#4f5536"))],
    )
  ]
  #block(width: 100%, inset: (x: 20pt, top: 15pt, bottom: 20pt))[
{{CONTENT}}
  ]
  #if ft [
    #block(width: 100%, height: 2pt, fill: rgb("#282a2e"))
    #block(width: 100%, fill: soft, radius: (bottom: 5pt), inset: (x: 18pt, y: 10pt))[
      #grid(columns: (auto, 1fr), column-gutter: 7pt, align: horizon,
        [#box(fill: rgb("#292b30"), radius: 2pt, inset: (x: 4pt, y: 2pt))[#text("REC", size: 6.7pt, weight: "bold", fill: accent)]],
        [#text(footer, size: 8pt, fill: muted)],
      )
    ]
  ]
]
