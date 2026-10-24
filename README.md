# PlainGov

What your government is doing, in plain English.

PlainGov watches Congress and all 50 state legislatures, rewrites new bills and milestones without jargon, publishes a simple page for each bill at [plaingov.com](https://plaingov.com), and posts the most interesting ones to [@plaingov](https://x.com/plaingov).

- **Congress:** [Congress.gov API](https://api.congress.gov) bill summaries
- **States:** each legislature's own website, scraped about daily with [Open States' open-source scrapers](https://github.com/openstates/openstates-scrapers): bills that passed a chamber, passed the legislature, became law, or were vetoed
- **AI:** Claude rewrites each update in plain English from the official record; [jev](https://typesafe.ai) scores how interesting it is, and only the best few per day get posted

## How it works

`plaingov.py` is one stdlib-only Python script, run hourly by systemd timers on an [exe.dev](https://exe.dev) VM:

```
python3 plaingov.py congress   # new Congress.gov summaries -> pages + post queue
python3 plaingov.py scrape     # daemon: scrape every state ~daily -> pages + post queue + alerts
python3 plaingov.py scrape SC  # scrape one state now
python3 plaingov.py post       # post the most interesting queued update to @plaingov
python3 plaingov.py site       # rebuild the static site
python3 test_plaingov.py       # self-check
```

A feed's first run builds pages for what's already listed and queues no posts. Milestone posts link to the bill's page; other posts don't (X charges far more for posts with links). Data and the generated site live in `~/plaingov-data`; `plaingov-web.service` serves it, and a Vercel project in front of plaingov.com proxies to it for Vercel Analytics.

AI calls go through exe.dev integrations, so no AI keys live on the VM: `llm.int.exe.xyz` (Claude) and `opencode.int.exe.xyz` (jev via OpenCode Zen).

## Config

Environment (`~/plaingov.env` on the VM, never committed):

| Variable | Purpose |
|---|---|
| `CONGRESS_API_KEY` | [api.data.gov key](https://api.congress.gov/sign-up/) |
| `NEW_YORK_API_KEY`, `INDIANA_API_KEY`, `VA_API_KEY` | free keys some state scrapers need |
| `LEGISCAN_API_KEY` | [LegiScan](https://legiscan.com/legiscan) key: sites that block us (IL, AK), states waiting on keys, and backup while a scraper fails |
| `ALERT_EMAIL` | where scraper alerts go (exe.dev only emails the VM owner) |
| `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | @plaingov's OAuth 1.0a keys (read+write) |
| `DRY_RUN` | `1` (default) prints posts instead of sending |
| `MAX_POSTS_PER_DAY`, `MIN_INTEREST`, `BACKFILL_DAYS`, `PARALLEL`, `RESCRAPE_HOURS`, `SITE_URL`, `LLM_MODEL`, `JEV_MODEL`, `DATA_DIR` | optional overrides |

## Monitoring

After every state scrape, `health.json` records status, bill counts, and errors, and [plaingov.com/status](https://plaingov.com/status/) shows it publicly. You get an email and a phone push (exe.dev) when a state's scraper fails, its bill count drops by half, it hasn't succeeded in 2 days, or it recovers. While a scraper fails, LegiScan fills in that state (at most once a day, under the scraper's own bill IDs so nothing posts twice) and /status says "Using backup source". Each alert checks the legislature's website so it says whether their site is down or our scraper needs a fix. An hourly GitHub Actions [watchdog](.github/workflows/watchdog.yml) fails (and GitHub emails you) if the site is down, the daemon stops writing `status/health.json`, or no state succeeds for a day, so it catches the VM itself dying.

## Deploy

```
git clone https://github.com/redreceipt/plaingov ~/plaingov
sudo cp ~/plaingov/*.service ~/plaingov/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload
sudo usermod -aG docker exedev
sudo systemctl enable --now plaingov-web plaingov-scrape plaingov@congress.timer plaingov@post.timer
```
