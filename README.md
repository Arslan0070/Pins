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

1. Create a new GitHub repo. **Make it Public** — this is required for the pin links (in the Excel file or the Sheet) to actually open for anyone.
2. Upload all files in this folder (`pin_generator.py`, `requirements.txt`, `assets/`, `.github/workflows/generate-pins.yml`).
3. Go to **Settings → Secrets and variables → Actions → New repository secret**:
   - Name: `SHEET_CSV_URL`
   - Value: the CSV link you copied in step 1.

## 3. Getting each pin's link (the simple way - recommended)

No extra setup needed for this — it just works. Every run automatically creates **`pinterest_bulk_upload.csv`** inside the `pins/` folder, formatted exactly for Pinterest's own bulk-upload tool: Title, Media URL, Pinterest board, Thumbnail, Description, Link, Publish date, Keywords.

- **Link** is the original article URL with `?utm_source=arslan&utm_medium=social&utm_campaign=arslan` automatically appended (change these via the `UTM_SOURCE` / `UTM_MEDIUM` / `UTM_CAMPAIGN` variables if you want).
- **Publish date** is filled in automatically: starting tomorrow, 10 pins per day, moving to the next day after every 10 rows. If a particular pin fails to generate, its row still gets a date (so the schedule never skips a day or shifts) — it just has blank Title/Media URL since there's nothing to upload for that one.
- **Pinterest board** defaults to `"Boredpanda Viral"` — change it with the `PINTEREST_BOARD` variable if you use a different board name.

It shows up in the same downloadable zip as the pin images (see step 5), and also gets committed into your repo alongside them.

## 4. (Optional, advanced) Writing links directly into your live Google Sheet instead

Skip this section unless you specifically want a "Pin Link" column to appear inside your actual Google Sheet, rather than just the Excel file above. This route needs a Google "service account" (a set of credentials that let the script log in and edit your sheet), which is more setup and more places for things to go wrong — the Excel file above gives you the same links with none of that hassle.

If you still want it:
1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a project → enable the **Google Sheets API** and **Google Drive API**.
2. **APIs & Services → Credentials → Create Credentials → Service account** → create it → **Keys** tab → **Add Key → Create new key → JSON**.
3. Open that JSON file, copy the `client_email` value, then share your Google Sheet with that email as **Editor**.
4. Copy your Sheet's ID from its URL: `https://docs.google.com/spreadsheets/d/THIS_PART/edit`
5. In GitHub, add two secrets: `GOOGLE_SERVICE_ACCOUNT_JSON` (paste the entire JSON file contents) and `SHEET_ID` (the ID from step 4).

If this isn't working, don't worry about troubleshooting it — just leave those two secrets unset (or delete them if you already added them). The script detects they're missing and simply skips this step; `pinterest_bulk_upload.csv` still gets created either way.

## 5. Let it run

- The workflow runs daily at 06:00 UTC automatically (edit the `cron` line in `generate-pins.yml` to change the time — cron times are in UTC).
- You can also trigger it manually anytime from the **Actions** tab → "Generate Pinterest Pins" → **Run workflow**.
- Each run commits that day's pins straight into a `pins/` folder in your repo (so the public links work), and also uploads **two separate downloadable artifacts**: one zip with just the pin images, and one with just `pinterest_bulk_upload.csv` — so you can grab either on its own from the workflow run's **Artifacts** section.

## 6. Running it locally instead (optional)

You don't need GitHub at all if you'd rather run it on your own machine:

```bash
pip install -r requirements.txt
export SHEET_CSV_URL="https://docs.google.com/spreadsheets/d/e/XXXXX/pub?output=csv"
python pin_generator.py
```

Pins are saved to the `pins/` folder. To automate this locally, add it to `cron` (Mac/Linux) or Task Scheduler (Windows) — but your machine needs to be on at that time, which is why GitHub Actions (cloud, free, always-on) is usually easier. (Note: the "Pin Link" write-back feature is designed around GitHub-hosted links, so it only makes sense when running through GitHub Actions.)

## Customizing the design

Open `pin_generator.py` and adjust the constants near the top:
- `GRADIENT_START` / `GRADIENT_END` — the pink→yellow band colors
- `PIN_WIDTH`, `IMAGE_HEIGHT`, `TITLE_BAND_HEIGHT` — overall pin dimensions
- `FONT_PATH` — swap in any `.ttf` you drop into `assets/`
- `MAX_FONT_SIZE` / `MIN_FONT_SIZE` — title text size range (auto-shrinks to fit long titles)

## Notes

- If a row's image fails to download (bad URL, broken link), that row is skipped and logged — it won't stop the other pins from generating.
- Output filenames are numbered by row + a slug of the title, so they sort in sheet order.
- The "Pin Link" write-back assumes your sheet's rows stay in the same order between runs (don't manually sort/filter the sheet in between) — it matches links back to rows by position, not by content.
- Committing pins into the repo daily means the repo grows over time (git doesn't delete old commits automatically). At ~200 pins/day this is fine for many months, but eventually you may want to prune old pins or move to a dedicated image host — just let me know if you get there.
