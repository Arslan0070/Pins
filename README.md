# Pinterest Pin Generator (100% free)

Builds pins in the layout: image / gradient title band / same image again — daily, automatically, from a Google Sheet.

## 1. Set up your Google Sheet

**If you only have article links** (e.g. `https://www.boredpanda.com/some-article/`), you just need ONE column:

| article_url |
|---|
| https://www.boredpanda.com/best-of-funny-photos-comedy-animals/ |
| https://www.boredpanda.com/another-article/ |

The script visits each link itself and automatically grabs that article's headline and main photo — no manual work needed. (It reads the same hidden tags a site uses to build a preview card when you paste its link into Facebook or Twitter.)

**If you already have the image link + title yourself**, use two columns instead:

| image_url | title |
|---|---|
| https://example.com/photo1.jpg | Stepmom Treats Stepdaughter's Labor Like A Premiere |

The script automatically detects which one of these your sheet is using — you don't need to configure anything.

Then: **File → Share → Publish to web → select the sheet tab → CSV → Publish**.
Copy the generated link (looks like `https://docs.google.com/spreadsheets/d/e/XXXXX/pub?output=csv`).

This link updates automatically whenever you edit the sheet, needs no login, and has no API quota.

## 2. Put this project on GitHub

1. Create a new **private** GitHub repo (private is fine and free).
2. Upload all files in this folder (`pin_generator.py`, `requirements.txt`, `assets/`, `.github/workflows/generate-pins.yml`).
3. Go to **Settings → Secrets and variables → Actions → New repository secret**:
   - Name: `SHEET_CSV_URL`
   - Value: the CSV link you copied in step 1.

## 3. Let it run

- The workflow runs daily at 06:00 UTC automatically (edit the `cron` line in `generate-pins.yml` to change the time — cron times are in UTC).
- You can also trigger it manually anytime from the **Actions** tab → "Generate Pinterest Pins" → **Run workflow**.
- When it finishes, open the workflow run → scroll to **Artifacts** → download the zip of that day's ~200 PNGs.

## 4. Running it locally instead (optional)

You don't need GitHub at all if you'd rather run it on your own machine:

```bash
pip install -r requirements.txt
export SHEET_CSV_URL="https://docs.google.com/spreadsheets/d/e/XXXXX/pub?output=csv"
python pin_generator.py
```

Pins are saved to the `output/` folder. To automate this locally, add it to `cron` (Mac/Linux) or Task Scheduler (Windows) — but your machine needs to be on at that time, which is why GitHub Actions (cloud, free, always-on) is usually easier.

## Customizing the design

Open `pin_generator.py` and adjust the constants near the top:
- `GRADIENT_START` / `GRADIENT_END` — the pink→yellow band colors
- `PIN_WIDTH`, `IMAGE_HEIGHT`, `TITLE_BAND_HEIGHT` — overall pin dimensions
- `FONT_PATH` — swap in any `.ttf` you drop into `assets/`
- `MAX_FONT_SIZE` / `MIN_FONT_SIZE` — title text size range (auto-shrinks to fit long titles)

## Notes

- If a row's image fails to download (bad URL, broken link), that row is skipped and logged — it won't stop the other ~199 pins from generating.
- Output filenames are numbered by row + a slug of the title, so they sort in sheet order.
