#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1280
HEIGHT = 720
BG = (0, 0, 0, 0)
TOP_BAR = (11, 15, 20, 190)
BOTTOM_BAR = (11, 15, 20, 194)
BUBBLE = (16, 24, 32, 212)
WHITE = (255, 255, 255, 255)
ACCENT = (122, 247, 208, 255)

FONT_REGULAR = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


def make_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGBA", (WIDTH, HEIGHT), BG)
    return image, ImageDraw.Draw(image)


def draw_base_overlay(output_path: Path) -> None:
    image, draw = make_canvas()
    title_font = load_font(FONT_BOLD, 28)
    sub_font = load_font(FONT_REGULAR, 20)
    footer_font = load_font(FONT_REGULAR, 22)

    draw.rectangle((0, 0, WIDTH, 78), fill=TOP_BAR)
    draw.text((42, 18), "Change Interface Language", fill=WHITE, font=title_font)
    draw.text((42, 49), "Settings > Language > English", fill=ACCENT, font=sub_font)

    draw.rectangle((0, HEIGHT - 78, WIDTH, HEIGHT), fill=BOTTOM_BAR)
    footer_text = "Change the app language in three clicks. Restart is required for the new language to take effect."
    draw.text((42, HEIGHT - 52), footer_text, fill=WHITE, font=footer_font)
    image.save(output_path)


def draw_step_overlay(text: str, width: int, output_path: Path) -> None:
    image, draw = make_canvas()
    font = load_font(FONT_REGULAR, 24)
    x, y, h = 36, 94, 62
    draw.rounded_rectangle((x, y, x + width, y + h), radius=20, fill=BUBBLE)
    draw.text((x + 22, y + 18), text, fill=WHITE, font=font)
    image.save(output_path)


def main() -> int:
    out_dir = Path("/tmp/onboard_outputs/overlays")
    out_dir.mkdir(parents=True, exist_ok=True)

    draw_base_overlay(out_dir / "base.png")
    draw_step_overlay("1. Open Settings menu", 340, out_dir / "step1.png")
    draw_step_overlay("2. Click Language and choose English", 470, out_dir / "step2.png")
    draw_step_overlay("3. Confirm restart to apply the new UI language", 600, out_dir / "step3.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
