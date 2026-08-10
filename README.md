# Capital Markets & Economy — Daily Dashboard

A dashboard that regenerates itself every weekday morning: business cycle
position, Treasury yield curve, CPI/jobs/Fed funds, and a handful of capital
markets headlines. Runs entirely on free infrastructure.

## How it works
- `fetch_and_render.py` pulls yields/CPI/unemployment/payrolls/Fed funds from
  **FRED** (Federal Reserve's free data API), indices/oil/VIX from **Yahoo
  Finance**, and headlines from a couple of **RSS feeds**.
- It classifies where the economy sits on the 5-phase business cycle using a
  simple rule-based heuristic (see `classify_cycle()` — tune the thresholds
  if your own read differs; this is a starting signal, not a verdict).
- It renders everything into `template.html` and writes `docs/index.html`.
- A **GitHub Actions** workflow runs that script on a schedule and commits
  the result. **GitHub Pages** serves `docs/index.html` as a live URL.

Total cost: $0. No server to maintain.

## One-time setup

1. **Create a GitHub repo** (public — GitHub Pages is free on public repos;
   if you want it private you'll need GitHub Pro).

2. **Push these files** to the repo root, preserving structure:
   ```
   fetch_and_render.py
   template.html
   requirements.txt
   .github/workflows/daily-update.yml
   docs/            (can start empty — the script fills it)
   ```

3. **Get a free FRED API key**: https://fred.stlouisfed.org/docs/api/api_key.html
   (instant signup, no cost, no approval wait).

4. **Add it as a repo secret**:
   Repo → Settings → Secrets and variables → Actions → New repository secret
   → Name: `FRED_API_KEY` → Value: (the key from step 3).

5. **Enable GitHub Pages**:
   Repo → Settings → Pages → Source: "Deploy from a branch" → Branch: `main`,
   folder: `/docs` → Save.

6. **Run it once manually** to generate the first version:
   Repo → Actions tab → "Daily Capital Markets Update" → Run workflow.
   After it finishes (~1 min), your dashboard is live at:
   `https://<your-username>.github.io/<repo-name>/`

From then on, it re-runs automatically every weekday morning and the page
updates itself — nothing to do on your end.

## Testing locally (optional)

```bash
pip install -r requirements.txt
export FRED_API_KEY=your_key_here
python fetch_and_render.py
open docs/index.html   # or just double-click it
```

## Notes / things you may want to adjust
- **Cron timing**: GitHub Actions cron is UTC and doesn't auto-shift for
  daylight saving, so the "7 AM ET" schedule will drift an hour twice a year.
  Fine for a daily brief; adjust the cron expression if you care about the
  exact hour.
- **Bloomberg data isn't included.** This runs on free public sources (FRED,
  Yahoo Finance). Bloomberg Terminal data requires a paid B-PIPE/Server API
  subscription to access outside the terminal itself.
- **Headlines are raw RSS titles**, not summarized — reliable and free, but
  less curated than the earlier one-off brief I put together by hand. If you
  want AI-summarized headlines instead, that would mean adding an
  ANTHROPIC_API_KEY secret and a summarization call in the script — happy to
  add that if you want it.
- **The business-cycle call is a heuristic**, not an NBER dating. Worth
  sanity-checking against your own read periodically, especially around
  turning points.
