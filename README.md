# PlainGov

What your government is doing, in plain English.

PlainGov watches Congress and all 50 state legislatures, rewrites new bills and milestones without jargon, publishes a simple page for each bill at [plaingov.com](https://plaingov.com), and posts the most interesting ones to [@plaingov](https://x.com/plaingov).

- **Congress:** [Congress.gov API](https://api.congress.gov): bills whose latest official action happened this week, explained from the bill text and CRS's summary when there is one
- **States:** each legislature's own website, scraped about daily with [Open States' open-source scrapers](https://github.com/openstates/openstates-scrapers): bills that passed a chamber, passed the legislature, became law, or were vetoed
- **AI:** where a legislature words a milestone in a way its scraper doesn't recognize ("Act No. 245", "Returned by Governor without approval"), GPT 6 Luna reads the history of recently active bills to tell whether each passed, became law, or was vetoed; Claude rewrites each update in plain English from the official record; [jev](https://typesafe.ai) scores how interesting it is, and only the best few per day get posted

## Our mission

**Everyone should be able to see what's happening in their federal and state governments, understand what it means, and easily take action.**

PlainGov exists to make government transparent, understandable, and accessible to everyone. Our goals are:

1. **Know what's happening.** Make it easy to stay informed about what federal and state governments are doing, including proposed legislation, decisions, and changes that affect people's lives.
2. **Understand what it means.** Explain government activity in clear, everyday language: what a bill or law actually does, what changed, where it stands, and why it matters—without jargon or requiring people to research it themselves.
3. **Get involved easily.** Make it straightforward to act on issues people care about by showing relevant, practical ways to participate in the civic process, without confusing steps or barriers.

The experience should make it simple to go from **what's happening** to **what it means** to **what I can do about it**. PlainGov helps people make their own informed decisions; it doesn't tell them what to think.

## How it works

`plaingov.py` is one stdlib-only Python script, run hourly by systemd timers on an [exe.dev](https://exe.dev) VM:

```
python3 plaingov.py congress   # new Congress.gov summaries -> pages + post queue
python3 plaingov.py scrape     # daemon: scrape every state ~daily -> pages + post queue + alerts
python3 plaingov.py scrape SC  # scrape one state now
python3 plaingov.py post       # post the most interesting queued update to @plaingov
python3 plaingov.py site       # rebuild the static site
python3 plaingov.py restage    # repair saved status fields + rebuild; no AI calls or posts
python3 plaingov.py refresh    # rewrite existing pages after a prompt change (PROMPT_VERSION)
python3 plaingov.py catchup    # pages for each state's 25 most recent laws and vetoes this year (no posts)
python3 plaingov.py report     # X engagement for the last 7 days (emailed weekly by the hourly post run)
python3 test_plaingov.py       # self-check
```

A feed's first run builds pages for what's already listed and queues no posts. Milestone posts link to the bill's page; other posts don't (X charges far more for posts with links). Posts go out 8am–9pm Eastern, spread evenly, since a new post's first hours decide its reach on X. Data and the generated site live in `~/plaingov-data`; `plaingov-web.service` serves it, and a Vercel project in front of plaingov.com proxies to it for Vercel Analytics.

AI calls go through exe.dev integrations, so no AI keys live on the VM: `llm.int.exe.xyz` (Claude) and `opencode.int.exe.xyz` (jev via OpenCode Zen).

## Config

Environment (`~/plaingov.env` on the VM, never committed):

| Variable | Purpose |
|---|---|
| `CONGRESS_API_KEY` | [api.data.gov key](https://api.congress.gov/sign-up/) |
| `NEW_YORK_API_KEY`, `INDIANA_API_KEY`, `VA_API_KEY` | free keys some state scrapers need |
| `LEGISCAN_API_KEY` | [LegiScan](https://legiscan.com/legiscan) key: sites that block us (IL, AK, NH), states waiting on keys, and backup while a scraper fails |
| `ALERT_EMAIL` | where scraper alerts go (exe.dev only emails the VM owner) |
| `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | @plaingov's OAuth 1.0a keys (read+write) |
| `DRY_RUN` | `1` (default) prints posts instead of sending |
| `MAX_PAGES_PER_DAY` | new AI-written pages per day across all feeds (default 200, about $0.80); newest first, the rest wait |
| `MAX_POSTS_PER_DAY`, `MIN_INTEREST`, `BACKFILL_DAYS`, `PARALLEL`, `RESCRAPE_HOURS`, `SITE_URL`, `LLM_MODEL`, `STAGE_MODEL`, `JEV_MODEL`, `DATA_DIR` | optional overrides |

## Monitoring

After every state scrape, `health.json` records status, bill counts, and errors, and [plaingov.com/status](https://plaingov.com/status/) shows it publicly. You get an email and a phone push (exe.dev) when a state's scraper fails, its bill count drops by half, it hasn't succeeded in 2 days, or it recovers. While a scraper fails, LegiScan fills in that state (at most once a day, under the scraper's own bill IDs so nothing posts twice) and /status says "Using backup source". Each alert checks the legislature's website so it says whether their site is down or our scraper needs a fix. An hourly GitHub Actions [watchdog](.github/workflows/watchdog.yml) fails (and GitHub emails you) if the site is down, the daemon stops writing `status/health.json`, or no state succeeds for a day, so it catches the VM itself dying.

## Status repairs

With feed jobs stopped and `~/plaingov-data/bills` backed up, run `python3 plaingov.py restage` after deploying a status fix. It corrects saved status fields and rebuilds pages without changing the posting queue. This preserves explanatory prose and marks corrected records for `refresh`. Review affected summaries against the official source and refresh them before relying on them; refresh uses the existing AI services. Unknown action chambers have no guessed next step. Alaska legislative resolves, California joint resolutions and explicitly identified constitutional amendment proposals do not use the law tracker. California's [Assembly Chief Clerk glossary](https://clerk.assembly.ca.gov/glossary-terms) confirms joint resolutions have no force of law and need no Governor's signature; this rule does not apply to other jurisdictions. Known resolution/amendment classifications survive backup-feed updates.

## Bill pages and action links

Explanations are short paragraphs, with essential conditions and effective dates preserved. Prompt version 6 groups related facts without repeating a summary, detail list and “what changes” section. Existing records still render safely while awaiting a rewrite.

The status card shows the latest explained action, an evidence-based next step, and expandable official history. State ingestion preserves the latest action’s chamber; an unknown chamber stays unknown. A normal procedural next step is not a scheduled vote. Enacted bills, vetoes, resolutions and constitutional amendments do not receive a generic passage CTA.

Massachusetts pages link to the relevant chamber or an exactly matched committee page from the official directory; unmatched committees retain the directory fallback. For chamber-stage bills, readers can expand current members’ names and public office phone/email links directly on the page. Contacts come from the same official roster row as the member’s profile ID, never name matching. Congress links to official chamber/committee directories; verified executive referrals link to the White House or Massachusetts Governor. Other jurisdictions and unknown districts retain official lookup tools. No calls or messages are sent automatically.

## Sponsor links

Congress ingestion preserves sponsor Bioguide IDs. Site rebuilds match them against the [House Clerk](https://clerk.house.gov/xml/lists/MemberData.xml) and [Senate](https://www.senate.gov/general/contact_information/senators_cfm.xml) current-member rosters. Verified matches link to official profiles/contact pages with the roster and check date. Senators link to Senate.gov's state contact directory, which lists both senators and their office details; individual senator sites blocked automated verification. The sponsor may not represent the reader.

The state pilot covers Massachusetts House and Senate members. Ingestion reads the explicit Presenter profile link on the official bill page, checks its displayed name against the scraper's sole primary sponsor, and preserves its profile ID. Rebuilds check that ID against the current official chamber directories. Committees, multiple presenters, mismatches and former members stay unlinked. Resolved Open States person IDs are also preserved; name placeholders are not IDs. Other states retain official representative finders, not guessed sponsor profiles.

Roster failures omit affected links; no cached membership is reused. Rebuilds make up to five public directory requests: federal rosters when sponsor IDs are present and Massachusetts member/committee directories when MA pages are present; MA ingestion adds one bill-page request. Each has a 10-second timeout. Existing records acquire IDs on subsequent ingestion; metadata-only rollout enrichment needs no AI rewrite or repost. No calls or messages are sent, and PlainGov collects no addresses.

## Deploy

```
git clone https://github.com/redreceipt/plaingov ~/plaingov
sudo cp ~/plaingov/*.service ~/plaingov/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload
sudo apt-get install -y poppler-utils   # pdftotext, for bills published as PDFs
sudo usermod -aG docker exedev
sudo systemctl enable --now plaingov-web plaingov-scrape plaingov@congress.timer plaingov@post.timer
```
