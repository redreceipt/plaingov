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
python3 plaingov.py sponsors CA # enrich existing scraped sponsor IDs for one state; no AI/posts
python3 plaingov.py refresh    # rewrite existing pages after a prompt change (PROMPT_VERSION)
python3 plaingov.py catchup    # pages for each state's 25 most recent laws and vetoes this year (no posts)
python3 plaingov.py report     # X engagement for the last 7 days (emailed weekly by the hourly post run)
python3 test_plaingov.py       # self-check
python3 test_state_rosters.py  # state roster parsers and current-office gates
python3 test_sponsors.py       # identity joins, transport, cache and enrichment
python3 test_state_feeds.py    # keyed feeds, cutover and bill documents
```

A feed's first run builds pages for what's already listed and queues no posts. Milestone posts link to the bill's page; other posts don't (X charges far more for posts with links). Posts go out 8am–9pm Eastern, spread evenly, since a new post's first hours decide its reach on X. Data and the generated site live in `~/plaingov-data`; `plaingov-web.service` serves it, and a Vercel project in front of plaingov.com proxies to it for Vercel Analytics.

AI calls go through exe.dev integrations, so no AI keys live on the VM: `llm.int.exe.xyz` (Claude) and `opencode.int.exe.xyz` (jev via OpenCode Zen).

## Config

Environment (`~/plaingov.env` on the VM, never committed):

| Variable | Purpose |
|---|---|
| `CONGRESS_API_KEY` | [api.data.gov key](https://api.congress.gov/sign-up/) |
| `NEW_YORK_API_KEY` | New York's free legislative API key |
| `INDIANA_API_KEY` | activated IGA token from `apitoken.request@iga.in.gov` ([docs](https://docs.api.iga.in.gov/)) |
| `VA_API_KEY` | activated Virginia LIS Partner API key ([registration](https://lis.virginia.gov/developers)); registration confirmation alone is not a key |
| `LEGISCAN_API_KEY` | [LegiScan](https://legiscan.com/legiscan) key: sites that block us (IL, AK, NH), states waiting on keys, and backup while a scraper fails |
| `ALERT_EMAIL` | where scraper alerts go (exe.dev only emails the VM owner) |
| `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | @plaingov's OAuth 1.0a keys (read+write) |
| `DRY_RUN` | `1` (default) prints posts instead of sending |
| `MAX_PAGES_PER_DAY` | new AI-written pages per day across all feeds (default 200, about $0.80); newest first, the rest wait |
| `MAX_POSTS_PER_DAY`, `MIN_INTEREST`, `BACKFILL_DAYS`, `PARALLEL`, `RESCRAPE_HOURS`, `SITE_URL`, `LLM_MODEL`, `STAGE_MODEL`, `JEV_MODEL`, `DATA_DIR` | optional overrides |

### Indiana / Virginia activation

Keep activated tokens in `~/plaingov.env` (mode `600`), never Git, PR evidence or command-line arguments. A missing key keeps the state on LegiScan. Only the selected state's key enters its scraper container. The container hook enforces verified HTTPS for both official APIs, overriding upstream's obsolete certificate bypasses. Indiana PDF requests use a browser-compatible PlainGov User-Agent; an HTML app shell is not accepted as bill text.

Deploy the code before adding keys and restarting `plaingov-scrape`; adding keys to the running daemon's environment file alone does not reload them. No feed data needs deletion. On the first successful native scrape after LegiScan, existing same-session bill URLs survive number-spacing/zero-padding differences, and already-published identical milestones are marked seen under their native keys without rewriting prose or replaying posts. Old seen keys and posting history remain intact; changed/new milestones use normal ingestion. Ambiguous saved bill IDs fail rather than guessing.

## Monitoring

After every state scrape, `health.json` records status, bill counts, and errors, and [plaingov.com/status](https://plaingov.com/status/) shows it publicly. You get an email and a phone push (exe.dev) when a state's scraper fails, its bill count drops by half, it hasn't succeeded in 2 days, or it recovers. While a scraper fails, LegiScan fills in that state (at most once a day, under the scraper's own bill IDs so nothing posts twice) and /status says "Using backup source". Each alert checks the legislature's website so it says whether their site is down or our scraper needs a fix. An hourly GitHub Actions [watchdog](.github/workflows/watchdog.yml) fails (and GitHub emails you) if the site is down, the daemon stops writing `status/health.json`, or no state succeeds for a day, so it catches the VM itself dying.

## Status repairs

With feed jobs stopped and `~/plaingov-data/bills` backed up, run `python3 plaingov.py restage` after deploying a status fix. It corrects saved status fields and rebuilds pages without changing the posting queue. This preserves explanatory prose and marks corrected records for `refresh`. Review affected summaries against the official source and refresh them before relying on them; refresh uses the existing AI services. Unknown action chambers have no guessed next step. Alaska legislative resolves, California joint resolutions and explicitly identified constitutional amendment proposals do not use the law tracker. California's [Assembly Chief Clerk glossary](https://clerk.assembly.ca.gov/glossary-terms) confirms joint resolutions have no force of law and need no Governor's signature; this rule does not apply to other jurisdictions. Known resolution/amendment classifications survive backup-feed updates.

## Bill pages and action links

Explanations are short paragraphs, with essential conditions and effective dates preserved. Prompt version 6 groups related facts without repeating a summary, detail list and “what changes” section. Existing records still render safely while awaiting a rewrite.

Bill pages use plain paragraphs for the explanation, effective date and current status, followed by contacts and one official bill/history link. There are no sections, section headings, status cards, expandable histories, repeated bill details or separate next-step text. Title-only pages show the opening summary and one clear warning that details and effects are unconfirmed, without older filler paragraphs.

Only verified sponsor contacts appear on bill pages, without generic committee directories or representative finders. Massachusetts sponsors also show public office phone/email links from the same official roster row as their profile ID. No calls or messages are sent automatically.

## Sponsor links

Congress ingestion preserves sponsor Bioguide IDs. Site rebuilds match them against the [House Clerk](https://clerk.house.gov/xml/lists/MemberData.xml) and [Senate](https://www.senate.gov/general/contact_information/senators_cfm.xml) current-member rosters. Verified matches link the sponsor’s name to an official profile/contact page. Senators link to Senate.gov's state contact directory, which lists both senators and their office details; individual senator sites blocked automated verification. The sponsor may not represent the reader.

Massachusetts retains its explicit official Presenter/current-directory check. Other states join LegiScan person IDs to Open States person IDs using shared Vote Smart/Ballotpedia identifiers, then require the same explicit profile URL/ID and full name on a current official legislative roster. Names and district URLs never establish a person's identity. Ingestion matches the exact state, session and bill number; ambiguous sessions, committees, conflicting identities and display-name mismatches stay unlinked.

`state_rosters.py` covers all 50 states, but coverage is evidence-dependent, not a promise of a link for every sponsor. Alabama, Hawaii and Idaho use labeled official directories. Oklahoma Senate is currently unsupported; Utah and New Hampshire House may establish membership without a usable destination. Blocked sources, former members and uncertain matches keep plain sponsor text. No guessed X profiles are added.

State identity/official-source lookups share a disk cache (up to 24 hours; failures one hour), with no expired-evidence fallback. Open States identity CSVs must be published within seven days; current office still requires the official roster. Supplemental LegiScan lookups are capped at 200/day across workers, separate from existing feed queries. HTTPS redirects cannot downgrade to HTTP. Roster requests use verified TLS and a 20-second timeout; bundled intermediates complete missing chains without adding trust anchors. Federal/MA rosters remain uncached.

For existing native-scraped pages, stop feed jobs and back up the data before running `python3 plaingov.py sponsors CA` (one state at a time), then `python3 plaingov.py site`. Enrichment streams saved bill files, changes only sponsor identifiers, and leaves prose, prompt version, queue, seen keys and posting history unchanged. The shared lookup cap bounds requests; rerun on later days for remaining bills. LegiScan-only historical pages gain IDs on subsequent ingestion. No calls or messages are sent, and PlainGov collects no addresses.

## Deploy

```
git clone https://github.com/redreceipt/plaingov ~/plaingov
sudo cp ~/plaingov/*.service ~/plaingov/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload
sudo apt-get install -y poppler-utils   # pdftotext, for bills published as PDFs
sudo usermod -aG docker exedev
sudo systemctl enable --now plaingov-web plaingov-scrape plaingov@congress.timer plaingov@post.timer
```
