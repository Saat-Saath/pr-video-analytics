# PR video analytics

Tracks views, likes, comments and shares for every PR video posted from three source sheets (Kalpana disease channels, Sanjay Agency, Internal PR) across Instagram, Facebook and YouTube, at **Day 2, Day 7 and Day 30 after upload**.

**Live dashboard:** https://sagarchauhan7792-bot.github.io/pr-video-analytics/ (served from `docs/`)

Every video, account and number in the dashboard links to the post on its platform.

## Milestones

Ages are counted in calendar days, India time. A video posted on the 1st is captured on the 3rd (Day 2), the 8th (Day 7) and the 31st (Day 30).

| Milestone | Window | Late after |
|---|---|---|
| Day 2 | ages 2–6 | day 3 |
| Day 7 | ages 7–29 | day 9 |
| Day 30 | age 30+ | day 35 |

Each video is fetched once per window. A capture past the grace days is kept and flagged late with the day it was taken. Rules live in `analytics_master_build/milestones.py`.

## How the numbers are collected

| Platform | Service | Script |
|---|---|---|
| Instagram | Apify Instagram Scraper (plays, likes, comments, date) | `apify_instagram.mjs` |
| Facebook, YouTube | Bright Data Scraper API | `brightdata_api_pipeline.mjs` |

Facebook hides the view count on many smaller reels. As a backup, `analytics_master_build/fb_views_backup.py` scans each page's Reels tab with Apify (only reels newer than the oldest missing one) and fills in just the view count; Bright Data's likes, comments and shares are kept. It runs inside `refresh.py finish` and is capped per day by `APIFY_FB_BACKUP_MAX_ITEMS` (Apify bills about $0.005 per reel scanned). Set `APIFY_FB_BACKUP=off` to disable it.

## Daily refresh

```bash
cp .env.example .env          # add API keys and the three Google Sheet IDs
python refresh.py start       # sync sheets, find videos due for Day 2/7/30, start scraper runs
python refresh.py finish      # repeat until it exits 0: merge, rebuild workbook, write docs/
git add docs && git commit -m "Daily refresh" && git push
```

`sync_sheets.py` reads each sheet's CSV export, keeps existing `VID-####` ids, adds new rows, and keeps a video's id when its link is replaced in the same row (e.g. a Drive file swapped for the live post link). Internal PR tabs prefer the live post link over the Drive file.

The private master list (`analytics_master_build/source_data.json`), the sheet IDs and the API keys stay out of this repo. It only contains public post links and public counts (`docs/data.json`).
