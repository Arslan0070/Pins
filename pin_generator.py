"""
Pinterest Pin Generator
------------------------
Reads a Google Sheet (published as CSV). Two modes, auto-detected from
your sheet's columns:

  MODE A - article link only (recommended for you):
    column: article_url
    The script visits each article page and automatically pulls out its
    headline and main photo (the same ones the site shows when you share
    it on Facebook/Twitter - called "Open Graph" tags).

  MODE B - you already have the image + title yourself:
    columns: image_url, title

For each row, builds a pin:
    [ image ]
    [ gradient band with wrapped, underlined title ]
    [ same image again ]
and saves it as a PNG in the output folder.

Usage:
    python pin_generator.py

Config is controlled via environment variables (set as GitHub Actions
secrets/vars, or just export them locally):
    SHEET_CSV_URL   - required. The published CSV link from your Google Sheet.
    OUTPUT_DIR      - optional. Defaults to "output".
    ARTICLE_COL     - optional. Defaults to "article_url".
    IMAGE_COL       - optional. Defaults to "image_url".
    TITLE_COL       - optional. Defaults to "title".
"""

import os
import re
import sys
import textwrap
import unicodedata
from io import BytesIO
from typing import Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SHEET_CSV_URL = os.environ.get("SHEET_CSV_URL", "").strip()
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
ARTICLE_COL = os.environ.get("ARTICLE_COL", "article_url")
IMAGE_COL = os.environ.get("IMAGE_COL", "image_url")
TITLE_COL = os.environ.get("TITLE_COL", "title")
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Canvas / layout
PIN_WIDTH = 1000
IMAGE_HEIGHT = 750       # height of each of the two photo blocks
TITLE_BAND_HEIGHT = 480  # height of the middle title band
GRADIENT_START = (236, 64, 122)   # pink  (matches reference top-left)
GRADIENT_END = (255, 200, 60)     # yellow/orange (matches reference top-right)
TITLE_COLOR = (20, 20, 20)
FONT_PATH = os.path.join(os.path.dirname(__file__), "assets", "Poppins-Bold.ttf")
MAX_FONT_SIZE = 64
MIN_FONT_SIZE = 32
SIDE_PADDING = 60

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:max_len] or "pin"


def fetch_image(url: str) -> Image.Image:
    resp = requests.get(url, timeout=20, headers=REQUEST_HEADERS)
    resp.raise_for_status()
    img = Image.open(BytesIO(resp.content)).convert("RGB")
    return img


def extract_title_and_image(article_url: str) -> Tuple[str, str]:
    """
    Visit an article page and pull out its headline + main photo.
    Most sites (including BoredPanda) tag these with "Open Graph" meta tags
    - the same tags Facebook/Twitter use to build a preview card when you
    paste a link. We read those tags directly instead of guessing.
    Falls back to the <title> tag and the first big <img> if OG tags are missing.
    """
    resp = requests.get(article_url, timeout=20, headers=REQUEST_HEADERS)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    def meta(*names):
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return None

    title = meta("og:title", "twitter:title")
    if not title and soup.title:
        title = soup.title.get_text(strip=True)

    image_url = meta("og:image", "og:image:secure_url", "twitter:image")
    if not image_url:
        # fallback: first reasonably sized <img> in the page
        for img_tag in soup.find_all("img"):
            src = img_tag.get("src") or img_tag.get("data-src")
            if src and src.startswith("http"):
                image_url = src
                break

    if not title or not image_url:
        raise ValueError(f"Could not find title/image on page (title={bool(title)}, image={bool(image_url)})")

    return title, image_url


def cover_resize(img: Image.Image, width: int, height: int) -> Image.Image:
    """Resize + center-crop an image to exactly fill width x height (like CSS object-fit: cover)."""
    src_w, src_h = img.size
    src_ratio = src_w / src_h
    dst_ratio = width / height

    if src_ratio > dst_ratio:
        # source is wider than target -> match height, crop width
        new_h = height
        new_w = int(new_h * src_ratio)
    else:
        new_w = width
        new_h = int(new_w / src_ratio)

    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return img.crop((left, top, left + width, top + height))


def make_gradient(width: int, height: int, start_rgb, end_rgb) -> Image.Image:
    """Diagonal-ish gradient built as a fast left-to-right horizontal blend."""
    base = Image.new("RGB", (width, 1), color=0)
    draw = ImageDraw.Draw(base)
    for x in range(width):
        t = x / max(width - 1, 1)
        r = int(start_rgb[0] + (end_rgb[0] - start_rgb[0]) * t)
        g = int(start_rgb[1] + (end_rgb[1] - start_rgb[1]) * t)
        b = int(start_rgb[2] + (end_rgb[2] - start_rgb[2]) * t)
        draw.point((x, 0), fill=(r, g, b))
    return base.resize((width, height))


def fit_font_and_wrap(draw, text, max_width, max_height, font_path):
    """Pick the largest font size (within bounds) whose wrapped text fits the box."""
    for size in range(MAX_FONT_SIZE, MIN_FONT_SIZE - 1, -2):
        font = ImageFont.truetype(font_path, size)
        # estimate chars-per-line from an average character width
        avg_char_w = font.getbbox("x")[2] - font.getbbox("x")[0] or size * 0.55
        chars_per_line = max(1, int(max_width / (avg_char_w * 0.62)))
        lines = textwrap.wrap(text, width=chars_per_line) or [text]

        line_heights = []
        total_h = 0
        max_line_w = 0
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            line_heights.append(h)
            total_h += h + 14  # line spacing
            max_line_w = max(max_line_w, w)

        if total_h <= max_height and max_line_w <= max_width:
            return font, lines, line_heights
    # fallback: smallest size regardless of fit
    font = ImageFont.truetype(font_path, MIN_FONT_SIZE)
    lines = textwrap.wrap(text, width=30) or [text]
    line_heights = [draw.textbbox((0, 0), l, font=font)[3] for l in lines]
    return font, lines, line_heights


def build_pin(image: Image.Image, title: str) -> Image.Image:
    canvas_h = IMAGE_HEIGHT * 2 + TITLE_BAND_HEIGHT
    canvas = Image.new("RGB", (PIN_WIDTH, canvas_h), "white")

    photo = cover_resize(image, PIN_WIDTH, IMAGE_HEIGHT)
    canvas.paste(photo, (0, 0))
    canvas.paste(photo, (0, IMAGE_HEIGHT + TITLE_BAND_HEIGHT))

    gradient = make_gradient(PIN_WIDTH, TITLE_BAND_HEIGHT, GRADIENT_START, GRADIENT_END)
    canvas.paste(gradient, (0, IMAGE_HEIGHT))

    draw = ImageDraw.Draw(canvas)
    max_text_width = PIN_WIDTH - 2 * SIDE_PADDING
    max_text_height = TITLE_BAND_HEIGHT - 80
    font, lines, line_heights = fit_font_and_wrap(draw, title, max_text_width, max_text_height, FONT_PATH)

    line_gap = 14
    total_h = sum(line_heights) + line_gap * (len(lines) - 1)
    y = IMAGE_HEIGHT + (TITLE_BAND_HEIGHT - total_h) // 2

    for line, lh in zip(lines, line_heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (PIN_WIDTH - w) // 2
        draw.text((x, y), line, font=font, fill=TITLE_COLOR)
        # underline
        underline_y = y + lh + 6
        draw.line((x, underline_y, x + w, underline_y), fill=TITLE_COLOR, width=4)
        y += lh + line_gap

    return canvas


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not SHEET_CSV_URL:
        print("ERROR: SHEET_CSV_URL environment variable is not set.", file=sys.stderr)
        print("Publish your Google Sheet as CSV (File > Share > Publish to web) and set that URL.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Fetching sheet: {SHEET_CSV_URL}")
    df = pd.read_csv(SHEET_CSV_URL)
    df.columns = [c.strip().lower() for c in df.columns]

    article_col = ARTICLE_COL.lower()
    image_col = IMAGE_COL.lower()
    title_col = TITLE_COL.lower()

    use_article_mode = article_col in df.columns
    use_direct_mode = image_col in df.columns and title_col in df.columns

    if not use_article_mode and not use_direct_mode:
        print(
            f"ERROR: sheet needs either an '{ARTICLE_COL}' column, "
            f"or both '{IMAGE_COL}' and '{TITLE_COL}' columns. Found: {list(df.columns)}",
            file=sys.stderr,
        )
        sys.exit(1)

    mode_name = "article-link" if use_article_mode else "direct image+title"
    print(f"Mode: {mode_name}")

    success, failed = 0, 0
    for i, row in df.iterrows():
        try:
            if use_article_mode:
                article_url = str(row.get(article_col, "")).strip()
                if not article_url or article_url.lower() == "nan":
                    continue
                title, image_url = extract_title_and_image(article_url)
            else:
                image_url = str(row.get(image_col, "")).strip()
                title = str(row.get(title_col, "")).strip()
                if not image_url or image_url.lower() == "nan":
                    continue

            out_name = f"{i+1:04d}-{slugify(title)}.png"
            out_path = os.path.join(OUTPUT_DIR, out_name)

            img = fetch_image(image_url)
            pin = build_pin(img, title)
            pin.save(out_path, "PNG")
            success += 1
            print(f"[OK]   {out_name}")
        except Exception as e:
            failed += 1
            print(f"[FAIL] row {i+1}: {e}", file=sys.stderr)

    print(f"\nDone. {success} pins created, {failed} failed. Output: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
