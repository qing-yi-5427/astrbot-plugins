"""Render the QQ summary with the palette and layout of the raffle help card."""

from functools import lru_cache
from io import BytesIO
from pathlib import Path

from .summary import RANK_LIMIT

WIDTH = 1080
GROUND = "#f7f7f7"
SURFACE = "#ffffff"
LINE = "#e7e7e7"
INK = "#151515"
MUTED = "#686868"
BLUE_FILL = "#bddbff"
BLUE_INK = "#1740b7"


def font_file(bold):
    style = "Bold" if bold else "Regular"
    for folder in ("/usr/share/fonts/opentype/noto", "/usr/share/fonts/noto-cjk"):
        path = Path(folder) / f"NotoSansCJK-{style}.ttc"
        if path.is_file():
            return str(path), 2
    custom = Path("/AstrBot/data/font.ttf")
    if custom.is_file():
        return str(custom), 0
    raise OSError("No Chinese font for the raffle summary")


def render_summary(stats, rankings):
    from PIL import Image, ImageDraw, ImageFont

    boards = (("参与次数榜", rankings["entered"]), ("中奖次数榜", rankings["awarded"]))
    row_count = max(1, *(len(rows) for _, rows in boards))
    board_top = 519
    board_bottom = board_top + 81 + 56 * row_count + 14
    image = Image.new("RGB", (WIDTH, board_bottom + 120), GROUND)
    draw = ImageDraw.Draw(image)

    @lru_cache(maxsize=None)
    def font(size, bold):
        path, index = font_file(bold)
        return ImageFont.truetype(path, size, index=index)

    def text(x, y, value, size, color=INK, *, bold=False, anchor="lm"):
        draw.text((x, y), str(value), font=font(size, bold), fill=color, anchor=anchor)

    def box(bounds, fill=SURFACE, outline=LINE, radius=25):
        draw.rounded_rectangle(
            bounds, radius=radius, fill=fill, outline=outline, width=2
        )

    draw.rectangle((0, 0, WIDTH, 106), fill=SURFACE)
    draw.line((0, 106, WIDTH, 106), fill=LINE, width=2)
    box((48, 24, 108, 84), BLUE_FILL, None, 18)
    draw.rounded_rectangle((63, 43, 93, 65), radius=3, outline=BLUE_INK, width=2)
    for y in (47, 53, 59):
        draw.line((74, y, 74, y + 3), fill=BLUE_INK, width=2)
    text(126, 54, "QQ 群抽奖", 34, bold=True)
    text(48, 169, "本群抽奖总结", 64, bold=True)
    text(
        50,
        232,
        f"云文档历史 {stats['archive']} 场  ·  机器人 {stats['live']} 场",
        31,
        MUTED,
    )

    metrics = (
        ("抽奖总场次", stats["total"]),
        ("累计参与人次", stats["people"]),
        ("有结果的场次", stats["with_results"]),
        ("记录中奖人次", stats["winners"]),
    )
    for index, (label, value) in enumerate(metrics):
        x = 48 + (index % 2) * 500
        y = 275 + (index // 2) * 117
        box((x, y, x + 484, y + 103))
        text(x + 22, y + 30, label, 27, MUTED)
        text(x + 22, y + 72, f"{value:,}", 40, bold=True)

    for column, (heading, rows) in enumerate(boards):
        x = 48 + column * 500
        box((x, board_top, x + 484, board_bottom))
        text(x + 24, board_top + 39, heading, 35, bold=True)
        draw.line((x + 2, board_top + 77, x + 482, board_top + 77), fill=LINE, width=2)
        if not rows:
            text(x + 24, board_top + 110, "暂无记录", 31, MUTED)
        for index, row in enumerate(rows):
            center = board_top + 108 + 56 * index
            highlight = row["rank"] <= 3
            box(
                (x + 25, center - 18, x + 74, center + 18),
                BLUE_FILL if highlight else GROUND,
                None,
                10,
            )
            text(
                x + 50,
                center,
                row["rank"],
                25,
                BLUE_INK if highlight else MUTED,
                bold=True,
                anchor="mm",
            )
            count = f"{row['count']:,} 次"
            available = 356 - draw.textlength(count, font=font(28, True))
            nickname = row["name"]
            suffix = ""
            while draw.textlength(nickname + suffix, font=font(30, False)) > available:
                nickname, suffix = nickname[:-1], "…"
            text(x + 89, center, nickname + suffix, 30)
            text(x + 459, center, count, 28, BLUE_INK, bold=True, anchor="rm")

    text(
        48,
        board_bottom + 38,
        f"各榜前 {RANK_LIMIT} 位 · 并列同名次 · 历史按登记昵称统计",
        26,
        MUTED,
    )
    text(48, board_bottom + 82, "发 /抽奖管理，在「抽奖总结」查看明细", 27, BLUE_INK)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
