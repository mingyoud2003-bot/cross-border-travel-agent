"""Generate architecture diagrams for the repository documentation.

Run with: python scripts/generate_diagrams.py
Requires the optional ``docs`` dependency group.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
BG = "#0c0e0d"
PANEL = "#191c1a"
LINE = "#3b413c"
TEXT = "#f5f1e8"
MUTED = "#9b9a95"
LIME = "#d9ff69"


def font(size: int, bold: bool = False):
    candidates = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def canvas(title: str, subtitle: str):
    image = Image.new("RGB", (1600, 900), BG)
    draw = ImageDraw.Draw(image)
    draw.text((90, 62), title, fill=TEXT, font=font(48, True))
    draw.text((92, 122), subtitle, fill=MUTED, font=font(21))
    draw.line((90, 170, 1510, 170), fill=LINE, width=2)
    return image, draw


def box(draw, xy, title, body, accent=LIME):
    draw.rounded_rectangle(xy, radius=22, fill=PANEL, outline=LINE, width=2)
    x1, y1, _, _ = xy
    draw.rounded_rectangle((x1 + 24, y1 + 22, x1 + 38, y1 + 36), radius=7, fill=accent)
    draw.text((x1 + 52, y1 + 17), title, fill=TEXT, font=font(23, True))
    for index, line in enumerate(body):
        draw.text((x1 + 25, y1 + 63 + index * 28), line, fill=MUTED, font=font(17))


def arrow(draw, start, end, label=""):
    draw.line((*start, *end), fill=LIME, width=3)
    x, y = end
    draw.polygon(((x, y), (x - 13, y - 7), (x - 13, y + 7)), fill=LIME)
    if label:
        draw.text(((start[0] + end[0]) // 2 - 35, start[1] - 28), label, fill=MUTED, font=font(15))


def generate_sequence():
    image, draw = canvas(
        "Integrated decision sequence",
        "One agent · user-grounded state · deterministic final composition",
    )
    boxes = [
        ((70, 300, 285, 500), "User", ["Natural language", "corrections"]),
        ((330, 300, 545, 500), "TravelState", ["slots", "provenance"]),
        ((590, 230, 805, 430), "Agent", ["intent", "tool routing"]),
        ((850, 220, 1080, 390), "Train", ["schedule", "provider status"]),
        ((850, 420, 1080, 590), "Mileage", ["exact formula", "value / mile"]),
        ((850, 620, 1080, 790), "Loyalty RAG", ["scope gate", "evidence IDs"]),
        ((1160, 360, 1510, 610), "Decision composer", ["versioned policy", "8-section JSON", "evidence + limits"]),
    ]
    for xy, title, body in boxes:
        box(draw, xy, title, body)
    arrow(draw, (285, 400), (330, 400))
    arrow(draw, (545, 400), (590, 400))
    arrow(draw, (805, 330), (850, 305))
    arrow(draw, (805, 350), (850, 505))
    arrow(draw, (805, 370), (850, 705))
    arrow(draw, (1080, 305), (1160, 425))
    arrow(draw, (1080, 505), (1160, 485))
    arrow(draw, (1080, 705), (1160, 545))
    image.save(DOCS / "agent-sequence.png", optimize=True)


def generate_interactions():
    image, draw = canvas(
        "Application architecture",
        "Same-origin web product with isolated sessions and local observability",
    )
    box(draw, (90, 290, 390, 550), "Web UI", ["conversation", "decision cards", "state + trace"], "#79a8ff")
    box(draw, (500, 240, 830, 600), "FastAPI", ["request validation", "session lifecycle", "safe errors", "static assets"], LIME)
    box(draw, (940, 220, 1260, 430), "Agent service", ["async session lock", "SQLite memory", "typed TravelState"], "#ff8a5b")
    box(draw, (940, 520, 1260, 730), "Trace store", ["tool calls", "status + latency", "token usage"], "#c291ff")
    box(draw, (1360, 315, 1530, 635), "Providers", ["OpenAI", "Transitous", "RAG"], LIME)
    arrow(draw, (390, 420), (500, 420), "HTTP")
    arrow(draw, (830, 380), (940, 330))
    arrow(draw, (830, 470), (940, 625))
    arrow(draw, (1260, 330), (1360, 430))
    image.save(DOCS / "agent-interactions.png", optimize=True)


if __name__ == "__main__":
    DOCS.mkdir(exist_ok=True)
    generate_sequence()
    generate_interactions()
