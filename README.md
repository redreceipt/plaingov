# PlainGov

What your government is doing, in plain English.

PlainGov watches new bills and updates, rewrites them without jargon, publishes a simple page for each bill, and posts it to X:

- **U.S. Congress** → [@plaingov](https://x.com/plaingov), from the [Congress.gov API](https://api.congress.gov)
- **South Carolina** → @plaingovsc, from the legislature's [ratifications log](https://www.scstatehouse.gov/rats2.php)

Summaries are written with AI from official records only, and every page links to the official record.

## How it works

`plaingov.py` is one stdlib-only Python script, run hourly by systemd timers on an [exe.dev](https://exe.dev) VM:

```
python3 plaingov.py congress   # new Congress.gov summaries -> pages + @plaingov
python3 plaingov.py sc         # new SC ratifications/governor actions -> pages + @plaingovsc
python3 plaingov.py site       # rebuild the static site
python3 test_plaingov.py       # self-check
```

A feed's first run builds pages for what's already listed and posts nothing. Data and the generated site live in `~/plaingov-data`; `plaingov-web.service` serves the site.

## Config

Environment (`~/plaingov.env` on the VM, never committed):

| Variable | Purpose |
|---|---|
| `CONGRESS_API_KEY` | [api.data.gov key](https://api.congress.gov/sign-up/) |
| `X_PLAINGOV_*`, `X_PLAINGOVSC_*` | `API_KEY`, `API_SECRET`, `ACCESS_TOKEN`, `ACCESS_SECRET` (OAuth 1.0a, read+write) |
| `DRY_RUN` | `1` (default) prints posts instead of sending |
| `SITE_URL`, `MAX_POSTS`, `LLM_MODEL`, `DATA_DIR` | optional overrides |

## Deploy

```
git clone https://github.com/redreceipt/plaingov ~/plaingov
sudo cp ~/plaingov/*.service ~/plaingov/*.timer /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now plaingov-web plaingov@congress.timer plaingov@sc.timer
```
