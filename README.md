# PR video analytics

Tracks views, likes, comments and shares for every PR video posted from three source sheets (Kalpana disease channels, Sanjay Agency, Internal PR) across Instagram, Facebook and YouTube.

**Live dashboard:** see the GitHub Pages link in this repo's About section (served from `docs/`).

Every video, account and number in the dashboard links to the post on its platform.

## How the numbers are collected

| Platform | Service | Script |
|---|---|---|
| Instagram | Apify Instagram Scraper (plays, likes, comments, date) | `apify_instagram.mjs` |
| Facebook, YouTube | Bright Data Scraper API | `brightdata_api_pipeline.mjs` |

Facebook doesn't show a public view count on some reels; those are listed as "No view count" in the dashboard's To fix section.

## Refresh

```bash
cp .env.example .env            # add your own keys
node apify_instagram.mjs --urls-file ig-urls.json --output outputs/ig-result.json
node brightdata_api_pipeline.mjs scrape --platform facebook --urls-file fb-urls.json --output outputs/fb-result.json
node brightdata_api_pipeline.mjs scrape --platform youtube --urls-file yt-urls.json --output outputs/yt-result.json
python analytics_master_build/merge_results.py outputs/ig-result.json outputs/fb-result.json outputs/yt-result.json
python analytics_master_build/build_workbook.py      # Excel master (kept locally)
python analytics_master_build/export_dashboard.py    # updates docs/ for the live dashboard
git add docs && git commit -m "Refresh data" && git push
```

`merge_results.py` reads the private master list (`analytics_master_build/source_data.json`), which stays out of this repo because it holds internal Google Sheet links. The public repo only contains public post links and public counts (`docs/data.json`).
