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

For each row, builds a pin (2:3 ratio, Pinterest's preferred size):
    [ image ]
    [ random-colored gradient band with wrapped title,
      text auto-switches black/white for readability ]
    [ same image again ]
    [ small "Arslan" watermark badge, bottom-right corner ]
and saves it as a compressed JPEG in the output folder (small file size,
no visible quality loss - see JPEG_QUALITY below).

If GOOGLE_SERVICE_ACCOUNT_JSON and SHEET_ID are set, it also writes each
pin's public GitHub link back into a "Pin Link" column in your live
Google Sheet (this part is optional - the script works fine without it,
it just skips this step).

It also always saves "pinterest_bulk_upload.csv" next to the pins -
formatted exactly for Pinterest's bulk-upload tool (Title, Media URL,
Pinterest board, Thumbnail, Description, Link, Publish date, Keywords).

Board and keywords: in "auto" board mode, each pin's board is chosen from
its article's niche (using the article's own tags/category plus words in the
title, matched against boards.json) and the Keywords column is filled with
related SEO keywords. Articles that match no niche use the default board in
boards.json. In "single" mode every pin uses PINTEREST_BOARD.

Bad rows are skipped, never left blank: if an article has no usable title or
image, or its link/image can't be loaded, that row is left out completely -
no pin, no CSV row, and no publish date is used up. (1000 links with 3 bad
ones = 997 pins and 997 CSV rows.)

Questions asked before it starts (typed prompt if you run this locally with
`python pin_generator.py`; shown as fillable fields on GitHub's "Run workflow"
button if run through Actions):
  - how many pins per day (controls the Publish date grouping)
  - what UTM parameters to append to each article link
  - image or video output
  - board mode: auto (by niche) or single (one fixed board)

If there are more than CSV_CHUNK_SIZE rows (default 100), the CSV is
split into multiple files (pinterest_bulk_upload_part1.csv, _part2.csv,
...) - each one's publish dates restart fresh from "tomorrow" again,
rather than continuing on from the previous file.

Usage:
    python pin_generator.py

Config is controlled via environment variables (set as GitHub Actions
secrets/vars or workflow inputs, or just export them locally). Leave
PINS_PER_DAY / UTM_QUERY / OUTPUT_FORMAT / BOARD_MODE unset to be prompted instead:
    SHEET_CSV_URL             - required. The published CSV link from your Google Sheet.
    OUTPUT_DIR                - optional. Defaults to "pins".
    ARTICLE_COL               - optional. Defaults to "article_url".
    IMAGE_COL                 - optional. Defaults to "image_url".
    TITLE_COL                 - optional. Defaults to "title".
    WATERMARK_TEXT            - optional. Defaults to "Arslan".
    JPEG_QUALITY              - optional. 1-95. Defaults to 85 (high quality, small file).
    PINTEREST_BOARD           - optional. The board used in "single" mode. Defaults to "Boredpanda Viral".
    BOARD_MODE                - optional. "auto" or "single". If unset, you'll be prompted (default: auto).
    WORKERS                   - optional. How many articles to process at the same time, 1-16 (default 6).
                                Higher = faster. Lower it if a site starts refusing requests (403/429).
    BOARDS_FILE               - optional. Path to the niche/board list. Defaults to boards.json next to this script.
    PINTEREST_THUMBNAIL       - optional. Defaults to "0:00" (matches Pinterest's template).
    PINS_PER_DAY              - optional. If unset, you'll be prompted (default if not interactive: 10).
    UTM_QUERY                 - optional. If unset, you'll be prompted. Set to an empty
                                value to add no UTM parameters at all.
                                Default: "utm_source=arslan&utm_medium=social&utm_campaign=arslan"
    PUBLISH_START_OFFSET_DAYS - optional. Defaults to 1 (schedule starts tomorrow).
    CSV_CHUNK_SIZE            - optional. Defaults to 100 (max rows per CSV file before splitting).
    GOOGLE_SERVICE_ACCOUNT_JSON - optional, advanced. The full contents of your service
                                account's JSON key file, pasted as one secret. Only
                                needed if you want links written directly into your
                                live Sheet instead of just using the CSV.
    SHEET_ID                  - optional, advanced. Required together with
                                GOOGLE_SERVICE_ACCOUNT_JSON to write links back.
    PIN_LINK_COL              - optional. Defaults to "Pin Link".
"""

import csv
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from io import BytesIO
from typing import Tuple
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SHEET_CSV_URL = os.environ.get("SHEET_CSV_URL", "").strip()
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "pins")
LATEST_RUN_DIR = os.environ.get("LATEST_RUN_DIR", "latest_run")
DEFAULT_OUTPUT_FORMAT = "image"
DESIGN_TEMPLATE = os.environ.get("DESIGN_TEMPLATE", "random").strip().lower()
VIDEO_MIN_SECONDS = 10
VIDEO_MAX_SECONDS = 25
ARTICLE_COL = os.environ.get("ARTICLE_COL", "article_url")
IMAGE_COL = os.environ.get("IMAGE_COL", "image_url")
TITLE_COL = os.environ.get("TITLE_COL", "title")
WATERMARK_TEXT = os.environ.get("WATERMARK_TEXT", "Arslan")
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "85"))

# Pinterest bulk-upload CSV formatting
PINTEREST_BOARD = os.environ.get("PINTEREST_BOARD", "Boredpanda Viral")
PINTEREST_THUMBNAIL = os.environ.get("PINTEREST_THUMBNAIL", "0:00")
DEFAULT_UTM_QUERY = "utm_source=arslan&utm_medium=social&utm_campaign=arslan"
DEFAULT_PINS_PER_DAY = 10
PUBLISH_START_OFFSET_DAYS = int(os.environ.get("PUBLISH_START_OFFSET_DAYS", "1"))
CSV_CHUNK_SIZE = int(os.environ.get("CSV_CHUNK_SIZE", "100"))

# Optional: write pin links back into the Google Sheet
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
SHEET_ID = os.environ.get("SHEET_ID", "").strip()
PIN_LINK_COL = os.environ.get("PIN_LINK_COL", "Pin Link")

# Set automatically by GitHub Actions - used to build each pin's public raw link
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "").strip()   # e.g. "Arslan0070/PinterestPins"
GITHUB_REF_NAME = os.environ.get("GITHUB_REF_NAME", "main").strip()   # e.g. "main"

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Canvas / layout - Pinterest's standard 2:3 ratio (1000 x 1500)
PIN_WIDTH = 1000
PIN_HEIGHT = 1500
IMAGE_HEIGHT = 600        # height of each of the two photo blocks
TITLE_BAND_HEIGHT = PIN_HEIGHT - (IMAGE_HEIGHT * 2)  # 300px middle band

FONT_PATH = os.path.join(os.path.dirname(__file__), "assets", "Poppins-Bold.ttf")
MAX_FONT_SIZE = 92
MIN_FONT_SIZE = 42
SIDE_PADDING = 50

# A pool of background color pairs (start -> end of the gradient band).
# One is picked at random for every pin so a batch of 200 doesn't look identical.
COLOR_PALETTES = [
    ((236, 64, 122), (255, 200, 60)),    # pink -> yellow
    ((79, 172, 254), (0, 242, 254)),     # blue -> cyan
    ((131, 58, 180), (253, 29, 29)),     # purple -> red
    ((17, 153, 142), (56, 239, 125)),    # teal -> green
    ((255, 94, 77), (255, 195, 113)),    # coral -> peach
    ((102, 126, 234), (118, 75, 162)),   # indigo -> purple
    ((250, 139, 255), (0, 219, 222)),    # magenta -> aqua
    ((247, 151, 30), (255, 210, 0)),     # orange -> gold
    ((0, 201, 255), (146, 254, 157)),    # sky -> mint
    ((238, 9, 121), (255, 106, 0)),      # hot pink -> orange
    ((30, 60, 114), (42, 82, 152)),      # navy -> steel blue
    ((252, 92, 125), (106, 130, 251)),   # rose -> periwinkle
    ((255, 61, 127), (255, 148, 114)),   # watermelon -> salmon
    ((0, 176, 155), (150, 201, 61)),     # emerald -> lime
    ((41, 128, 185), (109, 213, 250)),   # ocean blue -> light blue
    ((142, 45, 226), (74, 0, 224)),      # violet -> deep purple
    ((255, 0, 150), (0, 204, 255)),      # neon pink -> neon blue
    ((247, 121, 125), (255, 179, 71)),   # sunset red -> amber
    ((44, 62, 80), (52, 152, 219)),      # midnight -> sky blue
    ((203, 45, 62), (251, 196, 87)),     # crimson -> honey
    ((69, 39, 160), (149, 117, 205)),    # deep violet -> lilac
    ((255, 175, 189), (255, 195, 160)),  # blush pink -> peach pastel
    ((22, 160, 133), (241, 196, 15)),    # jade -> yellow
    ((192, 57, 43), (211, 84, 0)),       # brick red -> burnt orange
    ((41, 50, 65), (100, 110, 125)),     # charcoal -> steel grey
    ((255, 107, 107), (255, 230, 109)),  # coral red -> lemon
    ((72, 61, 139), (123, 104, 238)),    # dark slate blue -> medium purple
    ((0, 128, 128), (120, 210, 210)),    # teal -> pale cyan
    ((214, 51, 132), (247, 183, 51)),    # magenta rose -> marigold
    ((46, 49, 146), (60, 180, 210)),     # royal blue -> electric cyan
    ((255, 65, 108), (255, 75, 43)),     # ruby -> tangerine
    ((94, 231, 223), (150, 87, 230)),    # aqua -> purple
    ((247, 37, 133), (114, 9, 183)),     # hot pink -> deep purple
    ((58, 12, 163), (90, 150, 220)),     # cobalt -> sky
    ((249, 212, 35), (255, 90, 141)),    # sunflower -> pink
    ((11, 72, 107), (110, 165, 175)),    # deep teal -> soft cyan
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:max_len] or "pin"


DEFAULT_WORKERS = 50
MAX_WORKERS = 100
_thread_local = threading.local()


def get_session() -> requests.Session:
    """
    One reusable connection pool per worker thread - far faster than opening a brand-new
    connection for every request - with automatic retries (waiting a little longer each
    time) for temporary problems like "too many requests" (429) or server hiccups (5xx).
    """
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(REQUEST_HEADERS)
        retry = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session


def get_workers() -> int:
    """How many articles to process at the same time. WORKERS env var / workflow input, default 6."""
    env_val = os.environ.get("WORKERS", "").strip()
    if env_val:
        try:
            n = int(env_val)
            if n >= 1:
                return min(n, MAX_WORKERS)
        except ValueError:
            pass
        print(f"Invalid WORKERS '{env_val}', using default {DEFAULT_WORKERS}.", file=sys.stderr)
    return DEFAULT_WORKERS


def fetch_image(url: str) -> Image.Image:
    resp = get_session().get(url, timeout=20)
    resp.raise_for_status()
    img = Image.open(BytesIO(resp.content)).convert("RGB")
    return img


# Titles that mean "this page didn't really load" (blocked / error pages) - such rows are skipped
BAD_TITLE_STARTS = (
    "just a moment", "access denied", "attention required", "403 forbidden", "404 not found",
    "page not found", "not found", "forbidden", "bad gateway", "service unavailable", "too many requests",
)
SITE_NAME_TITLES = {"bored panda", "boredpanda"}
# Tags that say nothing about the topic
IGNORED_TAGS = {"bored panda", "boredpanda", "news", "article", "articles", "viral", "trending"}


def is_usable_title(title: str) -> bool:
    t = (title or "").strip().lower()
    if len(t) < 3 or t == "nan" or t in SITE_NAME_TITLES:
        return False
    return not t.startswith(BAD_TITLE_STARTS)


def _collect_ld_keywords(obj, out):
    """Walks JSON-LD data looking for 'keywords' / 'articleSection' values."""
    if isinstance(obj, dict):
        for key in ("keywords", "articleSection"):
            val = obj.get(key)
            if isinstance(val, str):
                out.extend(val.split(","))
            elif isinstance(val, list):
                out.extend(v for v in val if isinstance(v, str))
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _collect_ld_keywords(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_ld_keywords(v, out)


def clean_tags(raw_tags):
    tags, seen = [], set()
    for t in raw_tags:
        t = re.sub(r"\s+", " ", str(t)).strip().strip("#").lower()
        if not (3 <= len(t) <= 40) or t in IGNORED_TAGS or t in seen:
            continue
        seen.add(t)
        tags.append(t)
    return tags


_HEAD_END_RE = re.compile(r"</head>", re.I)
_JSON_LD_RE = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)


def _meta(soup, *names):
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def _find_title_and_image(soup):
    title = _meta(soup, "og:title", "twitter:title")
    if not title and soup.title:
        title = soup.title.get_text(strip=True)

    image_url = _meta(soup, "og:image", "og:image:secure_url", "twitter:image")
    if not image_url:
        # fallback: first reasonably sized <img> in the page
        for img_tag in soup.find_all("img"):
            src = img_tag.get("src") or img_tag.get("data-src")
            if src and src.startswith("http"):
                image_url = src
                break
    return title, image_url


def extract_article_info(article_url: str) -> dict:
    """
    Visit an article page and pull out: headline, main photo, and topic hints
    (the article's tags/category and its short description).
    Most sites (including BoredPanda) tag these with "Open Graph" meta tags
    - the same tags Facebook/Twitter use to build a preview card when you
    paste a link. We read those tags directly instead of guessing.
    Falls back to the <title> tag and the first big <img> if OG tags are missing.
    Raises ValueError if there is no usable title or image (the caller skips that row).

    Speed: those tags live in the page's <head>, so only that top part is parsed
    (about 500x faster than parsing a whole heavy article page). If the title or photo
    isn't found there, the whole page is read as a fallback.
    """
    resp = get_session().get(article_url, timeout=20)
    resp.raise_for_status()
    html = resp.text

    head_end = _HEAD_END_RE.search(html)
    soup = BeautifulSoup(html[: head_end.end()] if head_end else html, "html.parser")
    title, image_url = _find_title_and_image(soup)
    if head_end and (not title or not image_url):
        soup = BeautifulSoup(html, "html.parser")  # rare: this page keeps them outside the header
        title, image_url = _find_title_and_image(soup)

    if not is_usable_title(title):
        raise ValueError("no usable title on the page")
    if not image_url:
        raise ValueError("no image found on the page")

    # Topic hints: the article's own tags / category, wherever the page keeps them
    raw_tags = []
    for tag in soup.find_all("meta", attrs={"property": "article:tag"}):
        raw_tags.append(tag.get("content", ""))
    for name in ("article:section", "keywords", "news_keywords"):
        value = _meta(soup, name)
        if value:
            raw_tags.extend(value.split(","))
    for block in _JSON_LD_RE.findall(html):  # JSON-LD data can sit in the header or further down the page
        try:
            _collect_ld_keywords(json.loads(block), raw_tags)
        except (ValueError, TypeError):
            pass

    return {
        "title": title.strip(),
        "image_url": image_url,
        "tags": clean_tags(raw_tags),
        "description": _meta(soup, "og:description", "description") or "",
    }


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


def pick_text_color(start_rgb, end_rgb):
    """Choose black or white text depending on how light/dark the gradient is,
    so the title always stays readable no matter which random color was picked."""
    avg = tuple((a + b) // 2 for a, b in zip(start_rgb, end_rgb))
    luminance = 0.299 * avg[0] + 0.587 * avg[1] + 0.114 * avg[2]
    return (24, 24, 24) if luminance > 150 else (255, 255, 255)


def wrap_text_to_width(draw, text, font, max_width):
    """Word-wrap using real measured pixel widths (accurate, unlike character-count guessing)."""
    words = text.split()
    if not words:
        return [text]
    lines = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def fit_font_and_wrap(draw, text, max_width, max_height, font_path):
    """Pick the largest font size (within bounds) whose wrapped text fits the box."""
    best = None
    for size in range(MAX_FONT_SIZE, MIN_FONT_SIZE - 1, -2):
        font = ImageFont.truetype(font_path, size)
        lines = wrap_text_to_width(draw, text, font, max_width)
        line_gap = max(8, int(size * 0.22))

        line_heights, max_line_w, total_h = [], 0, 0
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            line_heights.append(h)
            max_line_w = max(max_line_w, w)
            total_h += h
        total_h += line_gap * (len(lines) - 1)

        if total_h <= max_height and max_line_w <= max_width:
            return font, lines, line_heights, line_gap
        best = (font, lines, line_heights, line_gap)  # remember smallest as fallback

    return best


def add_watermark(canvas: Image.Image, text: str) -> Image.Image:
    """Stamp a small semi-transparent name badge in the bottom-right corner of the pin."""
    canvas = canvas.convert("RGBA")
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font = ImageFont.truetype(FONT_PATH, 30)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]

    pad_x, pad_y, margin = 26, 14, 28
    badge_w, badge_h = text_w + pad_x * 2, text_h + pad_y * 2
    x0 = canvas.width - badge_w - margin
    y0 = canvas.height - badge_h - margin

    draw.rounded_rectangle((x0, y0, x0 + badge_w, y0 + badge_h), radius=badge_h // 2, fill=(0, 0, 0, 140))
    draw.text((x0 + pad_x, y0 + pad_y - bbox[1]), text, font=font, fill=(255, 255, 255, 235))

    return Image.alpha_composite(canvas, overlay).convert("RGB")


from PIL import ImageFilter

def smart_contain(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Scales image down to fit completely inside max_w x max_h without cropping."""
    src_w, src_h = img.size
    ratio = min(max_w / src_w, max_h / src_h)
    new_w, new_h = max(1, int(src_w * ratio)), max(1, int(src_h * ratio))
    return img.resize((new_w, new_h), Image.LANCZOS)

def build_classic_split(image: Image.Image, title: str, start_rgb, end_rgb, text_color, font_path) -> Image.Image:
    canvas = Image.new("RGB", (PIN_WIDTH, PIN_HEIGHT), "white")
    photo = cover_resize(image, PIN_WIDTH, IMAGE_HEIGHT)
    canvas.paste(photo, (0, 0))
    canvas.paste(photo, (0, IMAGE_HEIGHT + TITLE_BAND_HEIGHT))
    gradient = make_gradient(PIN_WIDTH, TITLE_BAND_HEIGHT, start_rgb, end_rgb)
    canvas.paste(gradient, (0, IMAGE_HEIGHT))

    draw = ImageDraw.Draw(canvas)
    max_text_width = PIN_WIDTH - 2 * SIDE_PADDING
    max_text_height = TITLE_BAND_HEIGHT - 60
    font, lines, line_heights, line_gap = fit_font_and_wrap(draw, title, max_text_width, max_text_height, font_path)

    total_h = sum(line_heights) + line_gap * (len(lines) - 1)
    y = IMAGE_HEIGHT + (TITLE_BAND_HEIGHT - total_h) // 2

    for line, lh in zip(lines, line_heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (PIN_WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, font=font, fill=text_color)
        y += lh + line_gap
    return canvas

def build_editorial(image: Image.Image, title: str, start_rgb, end_rgb, text_color, font_path) -> Image.Image:
    canvas = Image.new("RGB", (PIN_WIDTH, PIN_HEIGHT), "white")
    top_h = 1000
    bottom_h = PIN_HEIGHT - top_h
    photo = cover_resize(image, PIN_WIDTH, top_h)
    canvas.paste(photo, (0, 0))
    
    gradient = make_gradient(PIN_WIDTH, bottom_h, start_rgb, end_rgb)
    canvas.paste(gradient, (0, top_h))

    draw = ImageDraw.Draw(canvas)
    max_text_width = PIN_WIDTH - 2 * SIDE_PADDING
    max_text_height = bottom_h - 60
    font, lines, line_heights, line_gap = fit_font_and_wrap(draw, title, max_text_width, max_text_height, font_path)

    total_h = sum(line_heights) + line_gap * (len(lines) - 1)
    y = top_h + (bottom_h - total_h) // 2

    for line, lh in zip(lines, line_heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (PIN_WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, font=font, fill=text_color)
        y += lh + line_gap
    return canvas

def build_cinematic_blur(image: Image.Image, title: str, start_rgb, end_rgb, text_color, font_path) -> Image.Image:
    canvas = Image.new("RGB", (PIN_WIDTH, PIN_HEIGHT), "black")
    bg = cover_resize(image, PIN_WIDTH, PIN_HEIGHT)
    bg = bg.filter(ImageFilter.GaussianBlur(40))
    canvas.paste(bg, (0, 0))

    safe_h = PIN_HEIGHT - 350
    foreground = smart_contain(image, PIN_WIDTH - 60, safe_h)
    fg_x = (PIN_WIDTH - foreground.width) // 2
    fg_y = (safe_h - foreground.height) // 2 + 50
    
    # Optional drop shadow could go here, keeping simple for speed
    canvas.paste(foreground, (fg_x, fg_y))
    
    # Title band at bottom
    band_h = 300
    gradient = make_gradient(PIN_WIDTH, band_h, (20,20,20), (20,20,20)) # Dark transparent look
    canvas.paste(gradient, (0, PIN_HEIGHT - band_h))
    
    draw = ImageDraw.Draw(canvas)
    max_text_width = PIN_WIDTH - 2 * SIDE_PADDING
    max_text_height = band_h - 40
    font, lines, line_heights, line_gap = fit_font_and_wrap(draw, title, max_text_width, max_text_height, font_path)

    total_h = sum(line_heights) + line_gap * (len(lines) - 1)
    y = (PIN_HEIGHT - band_h) + (band_h - total_h) // 2

    for line, lh in zip(lines, line_heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (PIN_WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, font=font, fill=(255,255,255))
        y += lh + line_gap
    return canvas

def build_news_frame(image: Image.Image, title: str, start_rgb, end_rgb, text_color, font_path) -> Image.Image:
    canvas = Image.new("RGB", (PIN_WIDTH, PIN_HEIGHT), "white")
    gradient = make_gradient(PIN_WIDTH, PIN_HEIGHT, start_rgb, end_rgb)
    canvas.paste(gradient, (0, 0))

    fg = smart_contain(image, 900, 900)
    fg_x = (PIN_WIDTH - fg.width) // 2
    fg_y = (PIN_HEIGHT - fg.height) // 2
    canvas.paste(fg, (fg_x, fg_y))
    
    # Title split top and bottom
    draw = ImageDraw.Draw(canvas)
    words = title.split()
    mid = len(words) // 2
    title_top = " ".join(words[:mid])
    title_bottom = " ".join(words[mid:])
    
    # Top text
    if title_top:
        font_t, lines_t, lh_t, gap_t = fit_font_and_wrap(draw, title_top, 900, fg_y - 40, font_path)
        tot_t = sum(lh_t) + gap_t * (len(lines_t) - 1)
        y_top = (fg_y - tot_t) // 2
        for line, lh in zip(lines_t, lh_t):
            bbox = draw.textbbox((0, 0), line, font=font_t)
            x = (PIN_WIDTH - (bbox[2] - bbox[0])) // 2
            draw.text((x, y_top), line, font=font_t, fill=text_color)
            y_top += lh + gap_t

    # Bottom text
    if title_bottom:
        bottom_space = PIN_HEIGHT - (fg_y + fg.height)
        font_b, lines_b, lh_b, gap_b = fit_font_and_wrap(draw, title_bottom, 900, bottom_space - 60, font_path)
        tot_b = sum(lh_b) + gap_b * (len(lines_b) - 1)
        y_bot = fg_y + fg.height + (bottom_space - tot_b) // 2
        for line, lh in zip(lines_b, lh_b):
            bbox = draw.textbbox((0, 0), line, font=font_b)
            x = (PIN_WIDTH - (bbox[2] - bbox[0])) // 2
            draw.text((x, y_bot), line, font=font_b, fill=text_color)
            y_bot += lh + gap_b
            
    return canvas

def build_pin(image: Image.Image, title: str, template: str = "random") -> Image.Image:
    # Intelligent Aspect Ratio Routing
    src_w, src_h = image.size
    is_landscape = src_w > src_h
    
    # Define template pools
    safe_for_landscapes = ["cinematic-blur", "news-frame"]
    all_templates = ["classic-split", "editorial", "cinematic-blur", "news-frame"]
    
    # Route template
    if template == "random":
        if is_landscape:
            chosen = random.choice(safe_for_landscapes)
        else:
            chosen = random.choice(all_templates)
    else:
        # If a specific template is forced, check if it's safe. If forcing a crop on a landscape, override it to be safe (optional, but smart).
        if is_landscape and template in ["classic-split", "editorial"]:
            chosen = random.choice(safe_for_landscapes) # Auto-protect memes even if classic is selected
        else:
            chosen = template

    start_rgb, end_rgb = random.choice(COLOR_PALETTES)
    text_color = pick_text_color(start_rgb, end_rgb)
    
    if chosen == "classic-split":
        canvas = build_classic_split(image, title, start_rgb, end_rgb, text_color, FONT_PATH)
    elif chosen == "editorial":
        canvas = build_editorial(image, title, start_rgb, end_rgb, text_color, FONT_PATH)
    elif chosen == "cinematic-blur":
        canvas = build_cinematic_blur(image, title, start_rgb, end_rgb, text_color, FONT_PATH)
    elif chosen == "news-frame":
        canvas = build_news_frame(image, title, start_rgb, end_rgb, text_color, FONT_PATH)
    else:
        canvas = build_classic_split(image, title, start_rgb, end_rgb, text_color, FONT_PATH)

    return add_watermark(canvas, WATERMARK_TEXT)


def get_pins_per_day() -> int:
    """
    How many pins per day for the Publish date grouping. Priority:
    1) PINS_PER_DAY env var / workflow input, if set
    2) an interactive typed prompt, if running in a real terminal (local run)
    3) the default (10)
    """
    env_val = os.environ.get("PINS_PER_DAY", "").strip()
    if env_val:
        try:
            n = int(env_val)
            if n > 0:
                return n
        except ValueError:
            pass
        print(f"Invalid PINS_PER_DAY '{env_val}', using default {DEFAULT_PINS_PER_DAY}.", file=sys.stderr)
        return DEFAULT_PINS_PER_DAY

    if sys.stdin.isatty():
        raw = input(f"How many pins do you want to upload per day? [{DEFAULT_PINS_PER_DAY}]: ").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        return DEFAULT_PINS_PER_DAY

    return DEFAULT_PINS_PER_DAY


def get_utm_query() -> str:
    """
    The UTM query string appended to each article link (without the
    leading '?'), e.g. "utm_source=arslan&utm_medium=social&utm_campaign=arslan".
    Priority:
    1) UTM_QUERY env var / workflow input, if set (an empty value means "no UTM at all")
    2) an interactive typed prompt, if running in a real terminal (local run)
    3) the default
    """
    if "UTM_QUERY" in os.environ:
        return os.environ["UTM_QUERY"].strip().lstrip("?")

    if sys.stdin.isatty():
        raw = input(f"UTM parameters to append to each article link? [{DEFAULT_UTM_QUERY}]: ").strip()
        return raw.lstrip("?") if raw else DEFAULT_UTM_QUERY

    return DEFAULT_UTM_QUERY


def get_output_format() -> str:
    """
    "image" or "video". Priority:
    1) OUTPUT_FORMAT env var / workflow input, if set
    2) an interactive typed prompt, if running in a real terminal (local run)
    3) the default ("image")
    """
    env_val = os.environ.get("OUTPUT_FORMAT", "").strip().lower()
    if env_val in ("image", "video"):
        return env_val
    if env_val:
        print(f"Invalid OUTPUT_FORMAT '{env_val}', using default '{DEFAULT_OUTPUT_FORMAT}'.", file=sys.stderr)
        return DEFAULT_OUTPUT_FORMAT

    if sys.stdin.isatty():
        raw = input(f"Output format - image or video? [{DEFAULT_OUTPUT_FORMAT}]: ").strip().lower()
        if raw in ("image", "video"):
            return raw
        return DEFAULT_OUTPUT_FORMAT

    return DEFAULT_OUTPUT_FORMAT


def make_video_from_image(image_path: str, video_path: str) -> int:
    """
    Converts a static pin JPEG into a small MP4 (same visual, no motion) with
    a random duration between VIDEO_MIN_SECONDS and VIDEO_MAX_SECONDS.
    Uses a 1fps encode + '-tune stillimage' since the frame never changes,
    which keeps the file size small without sacrificing visible quality.
    Returns the duration (in seconds) used, for logging.
    Raises RuntimeError if ffmpeg isn't available or the encode fails.
    """
    duration = random.randint(VIDEO_MIN_SECONDS, VIDEO_MAX_SECONDS)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-framerate", "1",   # the picture never changes: read it at 1 frame/sec instead of ffmpeg's default 25 (about 10x faster)
        "-i", image_path,
        "-t", str(duration),
        "-r", "1",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-tune", "stillimage",
        "-crf", "30",
        "-movflags", "+faststart",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr[-500:]}")
    return duration


def check_ffmpeg_available():
    if shutil.which("ffmpeg") is None:
        print("ERROR: OUTPUT_FORMAT is 'video' but ffmpeg is not installed/available on this machine.", file=sys.stderr)
        print("Install ffmpeg (e.g. 'sudo apt-get install ffmpeg' on Ubuntu) and try again.", file=sys.stderr)
        sys.exit(1)


BOARD_MODES = ("auto", "single")
DEFAULT_BOARD_MODE = "auto"
BOARDS_FILE = os.environ.get("BOARDS_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "boards.json"))
MAX_KEYWORDS = 10

# Words that carry no topic meaning - ignored when turning a title into keywords
STOPWORDS = set("""
a about after again all also an and any are as at be because been before being but by can could did do does
doing down during each few for from further had has have having he her here hers him his how i if in into is
it its just me more most my no nor not now of off on once only or other our out over own same she should so
some such than that the their them then there these they this those through to too under until up very was
we were what when where which while who whom why will with would you your yours
things thing people times time photos photo pics pic pictures picture ever best worst top like get got
one two three four five six seven eight nine ten
won win wins use uses used using treat treats shut mad show shows showing shown reveal reveals revealed
say says said tell tells told take takes took make makes made give gives gave come comes came go goes went
find finds found look looks looked want wants wanted need needs needed think thinks thought know knows knew
try tries tried keep keeps kept let lets put puts call calls called ask asks asked turn turns turned
real really actually totally literally even still already always never every many much lot lots
good great bad big little new old first last next less
around between behind without within against among across
""".split())


def get_board_mode() -> str:
    """
    "auto" (board chosen by the article's niche) or "single" (one fixed board for every pin). Priority:
    1) BOARD_MODE env var / workflow input, if set
    2) an interactive typed prompt, if running in a real terminal (local run)
    3) the default ("auto")
    """
    env_val = os.environ.get("BOARD_MODE", "").strip().lower()
    if env_val in BOARD_MODES:
        return env_val
    if env_val:
        print(f"Invalid BOARD_MODE '{env_val}', using default '{DEFAULT_BOARD_MODE}'.", file=sys.stderr)
        return DEFAULT_BOARD_MODE

    if sys.stdin.isatty():
        raw = input(f"Board mode - auto (by niche) or single (one fixed board)? [{DEFAULT_BOARD_MODE}]: ").strip().lower()
        if raw in BOARD_MODES:
            return raw
        return DEFAULT_BOARD_MODE

    return DEFAULT_BOARD_MODE


def _word_pattern(word: str):
    # whole-word match that also accepts a simple plural (dog -> dogs, box -> boxes)
    return re.compile(r"(?<![a-z0-9])" + re.escape(word.lower()) + r"(?:s|es)?(?![a-z0-9])")


def load_boards_config(path: str = BOARDS_FILE) -> dict:
    """Reads boards.json. If it's missing or broken, falls back to the single default board so a run never crashes."""
    fallback = {"default_board": PINTEREST_BOARD, "niches": []}
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError) as e:
        print(f"WARNING: could not read {path} ({e}). Every pin will use the board '{PINTEREST_BOARD}'.", file=sys.stderr)
        return fallback

    niches = []
    for niche in config.get("niches", []):
        if not niche.get("board") or not niche.get("match"):
            continue
        niche["_patterns"] = [_word_pattern(w) for w in niche["match"]]
        niches.append(niche)
    return {"default_board": config.get("default_board") or PINTEREST_BOARD, "niches": niches}


def pick_niche(config: dict, title: str, tags, description: str):
    """
    Returns the best-matching niche dict, or None if nothing matches.
    Each different match-word found scores 2 points if it appears in the title or the
    article's tags/category, and 1 point if it only appears in the description.
    """
    strong_text = " ".join([title] + list(tags)).lower()
    weak_text = (description or "").lower()
    best, best_score = None, 0
    for niche in config["niches"]:
        score = 0
        for pattern in niche["_patterns"]:
            if pattern.search(strong_text):
                score += 2
            elif pattern.search(weak_text):
                score += 1
        if score > best_score:  # on a tie the niche listed first in boards.json wins
            best, best_score = niche, score
    return best


def build_keywords(title: str, tags, niche, max_keywords: int = MAX_KEYWORDS) -> str:
    """
    Comma-separated SEO keywords for the CSV Keywords column:
    up to 4 of the article's own tags, then up to 3 niche keywords from boards.json,
    then the meaningful words from the title, up to max_keywords in total.
    """
    keywords, seen = [], set()

    def add(word):
        word = word.strip().lower()
        if word and word not in seen and len(keywords) < max_keywords:
            seen.add(word)
            keywords.append(word)

    for tag in list(tags)[:4]:
        add(tag)
    if niche:
        for kw in niche.get("keywords", [])[:3]:
            add(kw)
    for token in re.findall(r"[a-z0-9']+", title.lower()):
        token = re.sub(r"'s$", "", token).strip("'")
        if len(token) >= 3 and token not in STOPWORDS and not token.isdigit():
            add(token)
    return ",".join(keywords)


def build_pin_link(filename: str) -> str:
    """Public raw GitHub URL for a pin file, once it's committed into the repo (requires a public repo)."""
    if not GITHUB_REPOSITORY:
        return ""
    return f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/{GITHUB_REF_NAME}/{OUTPUT_DIR}/{filename}"


def add_utm_params(url: str, utm_query: str) -> str:
    """Appends the given UTM query string to a URL without breaking any existing query params."""
    if not url or not utm_query:
        return url
    parts = urlparse(url)
    query = dict(parse_qsl(parts.query))
    query.update(parse_qsl(utm_query))
    return urlunparse(parts._replace(query=urlencode(query)))


def build_publish_dates(count: int, pins_per_day: int):
    """
    One date/time string per row. Starts PUBLISH_START_OFFSET_DAYS days from today, 
    and advances by one day every `pins_per_day` rows.
    Adds a random time between 07:00 and 22:00 to stagger uploads and avoid spam filters.
    """
    start = date.today() + timedelta(days=PUBLISH_START_OFFSET_DAYS)
    dates = []
    for i in range(count):
        day_offset = i // pins_per_day
        base_date = start + timedelta(days=day_offset)
        
        # Pick a random hour (7 AM to 10 PM) and random minute
        h = random.randint(7, 22)
        m = random.randint(0, 59)
        s = random.randint(0, 59)
        
        # Format: YYYY-MM-DD HH:MM:SS
        dt_str = f"{base_date.strftime('%Y-%m-%d')} {h:02d}:{m:02d}:{s:02d}"
        dates.append(dt_str)
    return dates


def save_pinterest_bulk_csv(rows, path, pins_per_day: int, output_format: str = "image"):
    """
    rows: list of dicts with keys "title", "media_url", "link", "board", "keywords" -
    one per successfully created pin (rows that failed are never included).
    Writes a CSV ready to import into Pinterest's bulk-upload tool.
    Per Pinterest's spec, the Thumbnail column is only used for video Pins
    and should be left blank for image Pins.
    """
    dates = build_publish_dates(len(rows), pins_per_day)
    thumbnail_value = PINTEREST_THUMBNAIL if output_format == "video" else ""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Title", "Media URL", "Pinterest board", "Thumbnail", "Description", "Link", "Publish date", "Keywords"])
        for row, publish_date in zip(rows, dates):
            writer.writerow([
                row.get("title", ""),
                row.get("media_url", ""),
                row.get("board") or PINTEREST_BOARD,
                thumbnail_value,
                "",
                row.get("link", ""),
                publish_date,
                row.get("keywords", ""),
            ])
    return path


def write_pin_links_to_sheet(pin_links_in_row_order):
    """
    Writes a 'Pin Link' column back into the live Google Sheet, one value per
    data row, in the same order the rows were read. Skipped/failed rows get
    a blank cell. Does nothing (safely) if Sheets credentials aren't configured.
    """
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not SHEET_ID:
        print("Skipping sheet write-back (GOOGLE_SERVICE_ACCOUNT_JSON / SHEET_ID not set).")
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("Missing packages for Sheets write-back. Install with: pip install gspread google-auth", file=sys.stderr)
        return

    try:
        creds_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        client = gspread.authorize(creds)
        ws = client.open_by_key(SHEET_ID).sheet1

        headers = ws.row_values(1)
        headers_lower = [h.strip().lower() for h in headers]
        if PIN_LINK_COL.lower() in headers_lower:
            col_index = headers_lower.index(PIN_LINK_COL.lower()) + 1
        else:
            col_index = len(headers) + 1
            ws.update_cell(1, col_index, PIN_LINK_COL)

        col_letter = gspread.utils.rowcol_to_a1(1, col_index).rstrip("0123456789")
        start_row, end_row = 2, len(pin_links_in_row_order) + 1
        range_name = f"{col_letter}{start_row}:{col_letter}{end_row}"
        values = [[link] for link in pin_links_in_row_order]

        ws.update(range_name=range_name, values=values)
        print(f"Wrote {len(values)} pin link(s) back into the '{PIN_LINK_COL}' column of your Sheet.")
    except Exception as e:
        print(f"Could not write pin links back to the Sheet: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_row(i, row, ctx) -> dict:
    """
    Turns ONE sheet row into a pin. Runs inside a worker thread, so several rows are
    processed at the same time. Never raises - it always returns a small result dict:
      {"status": "empty"}                  nothing in this row at all (ignored quietly)
      {"status": "skip", "reason": ...}    row couldn't become a pin (nothing is left behind)
      {"status": "ok", ...}                pin created; carries the CSV row, board, etc.
    """
    created_files = []  # files this attempt created in pins/ (cleaned up if the attempt fails part-way)
    try:
        if ctx["use_article_mode"]:
            article_url = str(row.get(ctx["article_col"], "")).strip()
            if not article_url or article_url.lower() == "nan":
                return {"status": "empty"}
            row_article_link = add_utm_params(article_url, ctx["utm_query"])
            info = extract_article_info(article_url)
            title, image_url, tags, description = info["title"], info["image_url"], info["tags"], info["description"]
        else:
            image_url = str(row.get(ctx["image_col"], "")).strip()
            title_value = str(row.get(ctx["title_col"], "")).strip()
            if not image_url or image_url.lower() == "nan":
                return {"status": "empty"}
            row_article_link = ""
            if not is_usable_title(title_value):
                raise ValueError("title is empty")
            title, tags, description = title_value, [], ""

        output_format = ctx["output_format"]
        out_name_jpg = f"{i+1:04d}-{slugify(title)}.jpg"
        jpg_path = os.path.join(OUTPUT_DIR, out_name_jpg)

        img = fetch_image(image_url)
        pin = build_pin(img, title, template=ctx.get("design_template", "random"))
        if not os.path.exists(jpg_path):
            created_files.append(jpg_path)
        pin.save(jpg_path, "JPEG", quality=JPEG_QUALITY, optimize=True)

        note = ""
        if output_format == "video":
            out_name = f"{i+1:04d}-{slugify(title)}.mp4"
            final_path = os.path.join(OUTPUT_DIR, out_name)
            if not os.path.exists(final_path):
                created_files.append(final_path)
            duration = make_video_from_image(jpg_path, final_path)
            os.remove(jpg_path)  # only the video is kept/hosted, the intermediate jpg isn't needed
            note = f"(video, {duration}s)"
        else:
            out_name = out_name_jpg
            final_path = jpg_path

        # Board + keywords for the CSV
        boards_config = ctx["boards_config"]
        niche = pick_niche(boards_config, title, tags, description) if boards_config else None
        if boards_config:
            board = niche["board"] if niche else boards_config["default_board"]
        else:
            board = PINTEREST_BOARD
        keywords = build_keywords(title, tags, niche)

        shutil.copy2(final_path, os.path.join(LATEST_RUN_DIR, out_name))  # copy for this run's download only - pins/ (permanent hosting) is untouched
        pin_link = build_pin_link(out_name)
        return {
            "status": "ok",
            "out_name": out_name,
            "pin_link": pin_link,
            "board": board,
            "note": note,
            "csv_row": {
                "title": title,
                "media_url": pin_link,
                "link": row_article_link,
                "board": board,
                "keywords": keywords,
            },
        }
    except Exception as e:
        # Skip this row completely: no pin, no CSV row, no date used up
        for path in created_files:
            try:
                if os.path.exists(path):
                    os.remove(path)  # only files this attempt created - nothing already hosted is ever touched
            except OSError:
                pass
        return {"status": "skip", "reason": str(e)[:120]}


def main():
    if not SHEET_CSV_URL:
        print("ERROR: SHEET_CSV_URL environment variable is not set.", file=sys.stderr)
        print("Publish your Google Sheet as CSV (File > Share > Publish to web) and set that URL.", file=sys.stderr)
        sys.exit(1)

    pins_per_day = get_pins_per_day()
    utm_query = get_utm_query()
    output_format = get_output_format()
    board_mode = get_board_mode()
    print(f"Pins per day: {pins_per_day}")
    print(f"UTM parameters: {utm_query or '(none)'}")
    print(f"Output format: {output_format}")
    print(f"Board mode: {board_mode}")

    boards_config = load_boards_config() if board_mode == "auto" else None

    if output_format == "video":
        check_ffmpeg_available()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(LATEST_RUN_DIR, exist_ok=True)

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

    workers = get_workers()
    print(f"Processing {len(df)} rows, {workers} at a time...")

    ctx = {
        "use_article_mode": use_article_mode,
        "article_col": article_col,
        "image_col": image_col,
        "title_col": title_col,
        "utm_query": utm_query,
        "output_format": output_format,
        "design_template": DESIGN_TEMPLATE,
        "boards_config": boards_config,
    }

    success = 0
    skipped = []                  # (sheet row number, reason) for every row that did not become a pin
    pinterest_rows = []           # one entry per SUCCESSFUL pin only - for the Pinterest bulk-upload CSV
    pin_links_in_row_order = []   # one entry per EVERY sheet row (blank if skipped) - for the optional Sheets write-back
    board_counts = {}

    rows = list(df.iterrows())
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(process_row, i, row, ctx) for i, row in rows]
        # Collect the results in the original sheet order, so the CSV rows (and their
        # publish dates) come out in exactly the same order as the sheet.
        for n, ((i, _), future) in enumerate(zip(rows, futures), start=1):
            result = future.result()
            if result["status"] == "ok":
                pinterest_rows.append(result["csv_row"])
                pin_links_in_row_order.append(result["pin_link"])
                board_counts[result["board"]] = board_counts.get(result["board"], 0) + 1
                success += 1
                print(f"[OK]   {result['out_name']}  -> board: {result['board']} {result['note']}".rstrip())
            elif result["status"] == "skip":
                pin_links_in_row_order.append("")
                skipped.append((i + 2, result["reason"]))  # +2 = spreadsheet row number (row 1 is the header)
                print(f"[SKIP] sheet row {i+2}: {result['reason']}", file=sys.stderr)
            else:  # empty row
                pin_links_in_row_order.append("")
            if n % 100 == 0:
                elapsed = time.time() - started
                print(f"--- {n}/{len(rows)} rows done ({elapsed/60:.1f} min elapsed) ---")

    print(f"Finished in {(time.time() - started)/60:.1f} min.")

    print(f"\nDone. {success} pins created, {len(skipped)} rows skipped. Output: {OUTPUT_DIR}/")
    if board_counts:
        print("Pins per board:")
        for board_name, count in sorted(board_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {count:>4}  {board_name}")
    if skipped:
        print(f"Skipped rows (sheet row number: reason) - first {min(len(skipped), 40)} of {len(skipped)}:")
        for row_number, reason in skipped[:40]:
            print(f"  row {row_number}: {reason}")

    if pinterest_rows:
        chunks = [pinterest_rows[i:i + CSV_CHUNK_SIZE] for i in range(0, len(pinterest_rows), CSV_CHUNK_SIZE)]
    else:
        chunks = []

    if len(chunks) <= 1:
        csv_path = os.path.join(OUTPUT_DIR, "pinterest_bulk_upload.csv")
        save_pinterest_bulk_csv(pinterest_rows, csv_path, pins_per_day, output_format)
        shutil.copy2(csv_path, os.path.join(LATEST_RUN_DIR, os.path.basename(csv_path)))
        print(f"Saved Pinterest bulk-upload CSV to: {csv_path}")
    else:
        for part_num, chunk in enumerate(chunks, start=1):
            csv_path = os.path.join(OUTPUT_DIR, f"pinterest_bulk_upload_part{part_num}.csv")
            save_pinterest_bulk_csv(chunk, csv_path, pins_per_day, output_format)  # dates restart fresh for each file
            shutil.copy2(csv_path, os.path.join(LATEST_RUN_DIR, os.path.basename(csv_path)))
            print(f"Saved Pinterest bulk-upload CSV part {part_num} ({len(chunk)} rows) to: {csv_path}")

    write_pin_links_to_sheet(pin_links_in_row_order)


if __name__ == "__main__":
    main()
