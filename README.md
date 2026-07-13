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

1. Create a new GitHub repo. **Make it Public** — this is required if you want the "Pin Link" write-back feature below (public raw links only work from a public repo). If you skip that feature, private is fine.
2. Upload all files in this folder (`pin_generator.py`, `requirements.txt`, `assets/`, `.github/workflows/generate-pins.yml`).
3. Go to **Settings → Secrets and variables → Actions → New repository secret**:
   - Name: `SHEET_CSV_URL`
   - Value: the CSV link you copied in step 1.

## 3. (Optional) Automatically write each pin's public link back into your Sheet

Skip this whole section if you're fine just grabbing pins from GitHub manually — everything above already works without it.

If you want a new **"Pin Link"** column to appear in your live Google Sheet with a clickable link to each pin, you need one extra piece: permission for the script to *write* to your Sheet (reading via the published CSV link doesn't allow writing).

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a project → enable the **Google Sheets API** and **Google Drive API** (APIs & Services → Library → search each → Enable).
2. **APIs & Services → Credentials → Create Credentials → Service account** → give it any name → Create and Continue → Done.
3. Click the service account → **Keys** tab → **Add Key → Create new key → JSON** → a `.json` file downloads. Keep this private — it's like a password.
4. Open that JSON file, copy the `client_email` value, then in your Google Sheet click **Share** and add that email as **Editor**.
5. Copy your Sheet's ID from its URL: `https://docs.google.com/spreadsheets/d/THIS_PART/edit`
6. Back in GitHub → **Settings → Secrets and variables → Actions**, add two more secrets:
   - Name: `GOOGLE_SERVICE_ACCOUNT_JSON` → Value: paste the **entire contents** of the JSON file you downloaded
   - Name: `SHEET_ID` → Value: the Sheet ID from step 5

That's it — no code changes needed. Next run, a "Pin Link" column appears automatically in your Sheet with each pin's public link (blank for any row that failed).

## 4. Let it run

- The workflow runs daily at 06:00 UTC automatically (edit the `cron` line in `generate-pins.yml` to change the time — cron times are in UTC).
- You can also trigger it manually anytime from the **Actions** tab → "Generate Pinterest Pins" → **Run workflow**.
- Each run commits that day's pins straight into a `pins/` folder in your repo (so the public links work), and also uploads them as a 14-day downloadable artifact as a backup.

## 5. Running it locally instead (optional)

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
