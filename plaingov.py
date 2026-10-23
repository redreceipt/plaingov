#!/usr/bin/env python3
"""PlainGov: what your government is doing, in plain English.

Usage: plaingov.py congress   new Congress.gov bill summaries -> pages + post queue
       plaingov.py scrape     daemon: run each state legislature's scraper about daily -> pages + post queue + alerts
       plaingov.py post       post the most interesting queued update to X (@plaingov)
       plaingov.py site       rebuild the site only

A feed's first run builds pages for what's already listed (states: last BACKFILL_DAYS) and queues no posts.
DRY_RUN=1 (default) prints posts instead of sending them.
"""
import base64, contextlib, fcntl, glob, hashlib, hmac, html, json, os, re, secrets, subprocess, sys, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

DATA = os.environ.get("DATA_DIR", os.path.expanduser("~/plaingov-data"))
SITE_URL = os.environ.get("SITE_URL", "https://plaingov.com").rstrip("/")
DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-5-5")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-1.13-free")
MAX_POSTS_PER_DAY = int(os.environ.get("MAX_POSTS_PER_DAY", "6"))
MIN_INTEREST = float(os.environ.get("MIN_INTEREST", "1.5"))  # jev score, 0-4
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "60"))
SCRAPER_IMAGE = os.environ.get("SCRAPER_IMAGE", "openstates/scrapers:latest")
PARALLEL = int(os.environ.get("PARALLEL", "12"))  # scrapers are network-bound (~85 MB, ~1% CPU each)
RESCRAPE_HOURS = float(os.environ.get("RESCRAPE_HOURS", "20"))
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "micneeley14@gmail.com")  # exe.dev only emails the VM owner
UA = "plaingov-bot/1.0 (+https://github.com/redreceipt/plaingov)"
URL_LEN = 23  # X counts every link as 23 chars


def fetch(url, data=None, headers={}):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def strip(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def load(path, default):
    return json.load(open(path)) if os.path.exists(path) else default


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


def stage(step, short, now, next=""):
    return {"step": step, "short": short, "now": now, "next": next}


# --- Congress -------------------------------------------------------------

TYPES = {
    "HR": ("H.R.", "house-bill"), "S": ("S.", "senate-bill"),
    "HJRES": ("H.J.Res.", "house-joint-resolution"), "SJRES": ("S.J.Res.", "senate-joint-resolution"),
    "HCONRES": ("H.Con.Res.", "house-concurrent-resolution"), "SCONRES": ("S.Con.Res.", "senate-concurrent-resolution"),
    "HRES": ("H.Res.", "house-resolution"), "SRES": ("S.Res.", "senate-resolution"),
}
RESOLUTIONS = {"HCONRES", "SCONRES", "HRES", "SRES"}  # these state a position; they never become law


def api(path, **params):
    q = urllib.parse.urlencode({"format": "json", "api_key": os.environ["CONGRESS_API_KEY"], **params})
    return json.loads(fetch(f"https://api.congress.gov/v3/{path}?{q}"))


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def us_stage(desc):
    ch = "House" if "House" in desc else "Senate"
    other = "Senate" if ch == "House" else "House"
    if "Public Law" in desc:
        return stage(3, "Became law", "It's now law.")
    if "agreed to" in desc:
        return stage(2, "Passed Congress", "The House and Senate both passed it.", "The President decides whether to sign it into law.")
    if desc.startswith("Passed"):
        return stage(1, f"Passed the {ch}", f"The {ch} voted to pass it.", f"The {other} needs to pass it too.")
    if desc.startswith("Reported"):
        return stage(0, f"Moving in the {ch}", f"A {ch} committee reviewed it and sent it to the full {ch}.", f"The full {ch} may vote on it.")
    who = "A member of the House" if ch == "House" else "A senator"
    return stage(0, f"Proposed in the {ch}", f"{who} proposed it.", f"A {ch} committee looks at it first. Most bills stop here.")


def sponsor_name(full):
    """'Sen. Hassan, Margaret Wood [D-NH]' -> 'Sen. Margaret Wood Hassan (D-NH)'"""
    m = re.match(r"(\S+) ([^,]+), (.+?) \[(\w+)-(\w+)", full or "")
    return f"{m[1]} {m[3]} {m[2]} ({m[4]}-{m[5]})" if m else full


def congress_items():
    # shortcut: newest 250 from the last 3 days; a burst of >250 between runs drops the overflow
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 86400))
    for s in reversed(api("summaries", limit=250, sort="updateDate desc", fromDateTime=since)["summaries"]):
        b = s["bill"]
        yield f"{b['congress']}-{b['type']}-{b['number']}-{s['versionCode']}", s


def congress_update(s):
    b, t = s["bill"], s["bill"]["type"]
    label, slug = TYPES[t]
    st = us_stage(s["actionDesc"])
    if t in RESOLUTIONS and st["step"] > 0:
        st["next"] = ""
    sponsors = api(f"bill/{b['congress']}/{t.lower()}/{b['number']}")["bill"].get("sponsors") or [{}]
    return {
        "id": f"us/{b['congress']}-{t.lower()}-{b['number']}", "where": "U.S. Congress", "state": None,
        "label": f"{label} {b['number']}", "resolution": t in RESOLUTIONS, "title": b["title"],
        "official_url": f"https://www.congress.gov/bill/{ordinal(b['congress'])}-congress/{slug}/{b['number']}",
        "sponsor": sponsor_name(sponsors[0].get("fullName")), "date": s["actionDate"], "stage": st,
        "milestone": st["step"] >= 1, "source": strip(s["text"]),
    }


# --- States: each legislature's own website, via Open States' open-source scrapers ---------

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
ASSEMBLY = {"CA", "NV", "NY", "NJ", "WI"}  # lower chamber is called the Assembly
KEYS = {"NY": "NEW_YORK_API_KEY", "IN": "INDIANA_API_KEY", "VA": "VA_API_KEY"}  # free keys from each legislature
# shortcut: California's scraper needs the state's database dumps loaded into MySQL; add a MySQL sidecar to cover it
UNSUPPORTED = {"CA": "California's scraper needs its database dumps loaded into MySQL first"}
LAW = {"became-law", "executive-signature", "veto-override-passage"}
lock = threading.Lock()  # scrapers finish in parallel; serialize shared files and site builds


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def org(org_id):
    """Open States org ref '~{"classification": "upper"}' -> 'upper'."""
    return json.loads(org_id[1:]).get("classification", "") if (org_id or "").startswith("~{") else ""


def bill_stage(bill):
    """(status, chamber, date) of a bill's furthest milestone: 2 passed one chamber, 3 passed both, 4 law, 5 vetoed."""
    passed, law, veto = {}, None, None
    for a in sorted(bill["actions"], key=lambda a: a["date"]):
        kinds, o, day = set(a["classification"]), org(a.get("organization_id")), a["date"][:10]
        if "passage" in kinds and o in ("upper", "lower", "legislature"):
            passed.setdefault(o, day)
        law = day if kinds & LAW else law
        veto = day if "executive-veto" in kinds else veto
    both = "legislature" in passed or {"upper", "lower"} <= set(passed)
    if law:
        return 4, None, law
    if veto and both:
        return 5, None, veto
    if both:
        return 3, None, max(passed.values())
    return (2, *next(iter(passed.items()))) if passed else None


def state_stage(st, status, chamber=None):
    lower = "Assembly" if st in ASSEMBLY else "House"
    ch = {"upper": "Senate", "lower": lower}.get(chamber, "")
    other = lower if ch == "Senate" else "Senate"
    return {
        2: stage(1, f"Passed the {ch}", f"The {ch} voted to pass it.", f"The {other} needs to pass it too."),
        3: stage(2, "Passed the Legislature", "Lawmakers passed it.", "The Governor decides whether to sign it."),
        4: stage(3, "Became law", "It's now law."),
        5: stage(2, "Vetoed", "The Governor rejected (vetoed) it.", "Lawmakers can still make it law by voting to override the veto."),
    }[status]


def scraped(st):
    return glob.glob(f"{DATA}/scrape/{st.lower()}/**/bill_*.json", recursive=True)  # scrapers write <datadir>/<state>/


def state_items(st):
    """Milestones in a state's latest scrape. Resolutions (commemorations, positions) never become law, so skip them."""
    for f in scraped(st):
        b = json.load(open(f))
        s = bill_stage(b) if set(b["classification"]) & {"bill", "joint resolution", "constitutional amendment"} else None
        if s:
            yield f"{st}-{b['legislative_session']}-{b['identifier']}-{s[0]}", {**b, "state": st, "status": s, "_date": s[2]}


def bill_text(b):
    """Newest HTML version of the bill, struck-out text removed, for richer rewrites."""
    for v in sorted(b["versions"], key=lambda v: v.get("date") or "", reverse=True):
        for link in v["links"]:
            if link.get("media_type") == "text/html":
                try:
                    return strip(re.sub(r"<(s|strike|del)\b.*?</\1>", " ", fetch(link["url"]), flags=re.S | re.I))
                except Exception:
                    return ""
    return ""


def state_update(b):
    st, (status, chamber, day) = b["state"], b["status"]
    people = [s for s in b["sponsorships"] if s["primary"]] or b["sponsorships"]
    sponsor = None
    if people:
        title = ("Sen. " if org(b["from_organization"]) in ("upper", "legislature") else "Rep. ") if people[0].get("entity_type") == "person" else ""
        more = len(people) - 1
        sponsor = title + people[0]["name"] + (f" and {more} other{'s' if more > 1 else ''}" if more else "")
    abstract = " ".join(a["abstract"] for a in b["abstracts"])
    # shortcut: first 12k chars only; huge bills (budgets) get summarized from their opening sections
    source = " ".join(x for x in (b["title"], abstract, bill_text(b)) if x)[:12000]
    return {
        "id": f"states/{st.lower()}/{slug(b['legislative_session'])}-{slug(b['identifier'])}", "where": STATES[st], "state": st,
        "label": b["identifier"], "resolution": False, "title": b["title"], "official_url": (b["sources"] or [{"url": ""}])[0]["url"],
        "sponsor": sponsor, "date": day, "stage": state_stage(st, status, chamber), "milestone": True, "source": source,
    }


# --- Scraper runs and monitoring ---------------------------------------------

HEALTH = f"{DATA}/state/health.json"


pending = []  # alerts are batched so a mass failure sends one digest, not one email per state


def alert(subject, body):
    with lock:
        pending.append((subject, body))
    print(f"ALERT {subject}", flush=True)


def flush_alerts():
    """Email (exe.dev gateway) + phone push (exe.dev notify) one digest. Never let alerting break a run."""
    with lock:
        batch = pending[:]
        pending.clear()
    if not batch:
        return
    subject = batch[0][0] if len(batch) == 1 else f"PlainGov: {len(batch)} scraper alerts"
    body = "\n\n----\n\n".join(f"{s}\n\n{b}" for s, b in batch)
    push = "; ".join(s.removeprefix("PlainGov: ") for s, _ in batch)
    for url, payload in (("http://169.254.169.254/gateway/email/send", {"to": ALERT_EMAIL, "subject": subject, "body": body}),
                         ("https://notify.int.exe.xyz/", {"title": subject, "body": push[:300]})):
        try:
            fetch(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        except Exception as ex:
            print(f"alert via {url} failed: {ex!r}", file=sys.stderr, flush=True)


def site_status(url):
    """Is the legislature's own website up? Tells 'their site is down' apart from 'our scraper broke'."""
    if not url:
        return "unknown"
    try:
        fetch(url)
        return "up"
    except Exception as ex:
        return f"down ({getattr(ex, 'code', None) or type(ex).__name__})"


def set_health(st, **fields):
    with lock:
        health = load(HEALTH, {})
        health[st] = {**health.get(st, {}), **fields}
        write(HEALTH, json.dumps(health, indent=1))
        return health[st]


def scrape_state(st):
    """Run one state's scraper, publish its new milestones, and alert when its health changes."""
    name, prev = STATES[st], load(HEALTH, {}).get(st, {})
    if st in UNSUPPORTED or (st in KEYS and not os.environ.get(KEYS[st])):
        return set_health(st, status="needs setup", error=UNSUPPORTED.get(st) or f"needs {KEYS[st]}", last_start=time.time())
    out = f"{DATA}/scrape/{st.lower()}"
    os.makedirs(out, exist_ok=True)
    # The image only runs as root (its Python env lives in /root), so clean up and hand files back via the image too.
    in_box = lambda *cmd: subprocess.run(["docker", "run", "--rm", "-v", f"{out}:/data", "--entrypoint", cmd[0], SCRAPER_IMAGE, *cmd[1:]], capture_output=True)
    in_box("find", "/data", "-mindepth", "1", "-delete")
    set_health(st, status="running", last_start=time.time())
    box = f"plaingov-scrape-{st.lower()}"
    subprocess.run(["docker", "rm", "-f", box], capture_output=True)  # left over if the daemon restarted mid-scrape
    cmd = ["docker", "run", "--rm", "--name", box, "-v", f"{out}:/data",
           *[a for k in KEYS.values() if os.environ.get(k) for a in ("-e", k)],
           SCRAPER_IMAGE, st.lower(), "bills", "--scrape", "--datadir", "/data"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=22 * 3600)
        ok, log = p.returncode == 0, (p.stdout + p.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        ok, log = False, "scraper still running after 22 hours"
    in_box("chown", "-R", f"{os.getuid()}:{os.getgid()}", "/data")
    files = scraped(st)
    bills = len(files)
    sessions = sorted({json.load(open(f))["legislative_session"] for f in files})
    # A new session legitimately starts small; only a drop within the same session(s) means the site changed.
    same = prev.get("bills") and prev.get("sessions") == sessions
    error = "" if ok else log
    if ok and same and bills < prev["bills"] * 0.5:
        error = f"only {bills} bills, down from {prev['bills']} in the same session; the site may have changed"
    page = (json.load(open(files[0]))["sources"] or [{}])[0].get("url", "") if files else ""
    site = ("{0.scheme}://{0.netloc}/".format(urllib.parse.urlparse(page)) if page else "") or prev.get("site")
    status = "failed" if error else "ok"
    stats = {}
    if status == "ok":
        new, done, failed = process(f"states-{st.lower()}", state_items(st), state_update)
        stats = {"last_ok": time.time(), "bills": bills, "sessions": sessions, "milestones": new, "page_failures": failed}
    h = set_health(st, status=status, error=error[-800:], last_end=time.time(), site=site, **stats)
    if status == "failed" and prev.get("status") != "failed":
        up = site_status(site)
        cause = (f"The {name} legislature's website looks {up}, so this is probably on their end." if up.startswith("down")
                 else f"The {name} legislature's website is {up}, so the scraper probably needs a fix (their site may have changed).")
        alert(f"PlainGov: {name} scraper failed", f"{cause}\n\nError:\n{error[-1500:]}\n\nStatus page: {SITE_URL}/status/\n"
              f"Rerun: ssh plaingov.exe.xyz 'cd plaingov && python3 plaingov.py scrape {st}'")
    elif status == "ok" and prev.get("status") == "failed":
        alert(f"PlainGov: {name} scraper recovered", f"{name} scraped {bills} bills successfully again.")
    return h


def check_stale(health):
    """Alert once when a state hasn't had a good scrape in 2 days (e.g. it keeps timing out)."""
    for st, h in health.items():
        if h.get("status") != "needs setup" and time.time() - h.get("last_ok", time.time()) > 48 * 3600 and not h.get("stale_alerted"):
            alert(f"PlainGov: {STATES[st]} hasn't updated in 2 days", f"Last error:\n{h.get('error', '')}\n\nStatus page: {SITE_URL}/status/")
            set_health(st, stale_alerted=True)
        elif h.get("stale_alerted") and time.time() - h.get("last_ok", 0) < 48 * 3600:
            set_health(st, stale_alerted=False)


def scrape(only=None):
    """Daemon: keep PARALLEL scrapers busy, each state about every RESCRAPE_HOURS; pull scraper fixes daily."""
    if only:
        print(scrape_state(only.upper()))
        return flush_alerts()
    pool, running, pulled, started = ThreadPoolExecutor(PARALLEL), {}, 0, time.time()
    for st, h in load(HEALTH, {}).items():  # scrapes cut off by a restart start over right away
        if h.get("status") == "running":
            subprocess.run(["docker", "rm", "-f", f"plaingov-scrape-{st.lower()}"], capture_output=True)
            set_health(st, last_start=0)
    while True:
        if time.time() - pulled > 86400:
            subprocess.run(["docker", "pull", "-q", SCRAPER_IMAGE], capture_output=True)
            pulled = time.time()
        for st, f in list(running.items()):
            if f.done():
                running.pop(st)
                if f.exception():
                    set_health(st, status="failed", error=repr(f.exception()), last_end=time.time())
                    alert(f"PlainGov: {STATES[st]} run crashed", repr(f.exception()))
        health = load(HEALTH, {})
        due = sorted((st for st in STATES if st not in running and time.time() - health.get(st, {}).get("last_start", 0) > RESCRAPE_HOURS * 3600),
                     key=lambda st: health.get(st, {}).get("last_start", 0))
        for st in due[:PARALLEL - len(running)]:
            running[st] = pool.submit(scrape_state, st)
        check_stale(health)
        flush_alerts()
        # Public heartbeat for the off-box watchdog (.github/workflows/watchdog.yml): daemon alive + states succeeding.
        write(f"{DATA}/site/status/health.json", json.dumps({"updated": time.time(), "started": started, "states": {
            st: {k: h.get(k) for k in ("status", "last_ok", "bills")} for st, h in health.items()}}))
        time.sleep(60)


# --- AI: plain-English rewrite (Claude) and interest score (jev) ----------------

PROMPT = """You write for PlainGov, which explains government to everyday people.
Rewrite this {where} bill for a general audience, at about an 8th-grade reading level.

Rules:
- Use only facts in the source. Don't guess about effects, costs, or motives.
- Neutral and nonpartisan. No opinions, hype words, emojis, or hashtags.
- No jargon or legal terms (like "appropriations", "amend", "statute", "section 8-11-155", "local educational agency"). Say it the way you'd explain it to a neighbor.
- Headline and summary: present tense, starting with a verb where natural ("Gives...", "Requires...").
- Tweet: match the status. Unless it's law, say what it would do ("would", "if passed"); never imply it already happened.
- If the source is brief, keep it brief and describe only what it says. Never mention the source or what it leaves out. Always reply with the JSON.

Reply with only a JSON object with these keys:
"headline": what it does in 4-9 words, sentence case, no period (max 70 characters)
"tweet": one sentence that makes a regular person stop scrolling: lead with who it affects or what changes for them, in concrete terms (max 140 characters)
"summary": at most 3 short sentences on what it does and who it affects (max 350 characters)

Official name: {title}
Status: {status}
Source: {source}"""


def rewrite(rec):
    body = json.dumps({"model": LLM_MODEL, "max_tokens": 600, "thinking": {"type": "disabled"},
                       "messages": [{"role": "user", "content": PROMPT.format(**rec, status=rec["stage"]["now"])}]})
    r = json.loads(fetch("https://llm.int.exe.xyz/v1/messages", body.encode(),
                         {"content-type": "application/json", "anthropic-version": "2023-06-01"}))
    text = "".join(c["text"] for c in r["content"] if c["type"] == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON from LLM (stop_reason={r.get('stop_reason')}): {text[:200]!r}")
    out = {k: " ".join(str(v).split()) for k, v in json.loads(m.group(0)).items()}
    if re.search(r"\b(the|this) source\b|\bsource text\b|\bfull text\b|\b(text|title) (does not|doesn't)\b", out["summary"] + out["tweet"], re.I):
        raise ValueError(f"rewrite talks about its source: {out['summary'][:200]!r}")
    return {k: out[k] for k in ("headline", "tweet", "summary")}


INTEREST = {"type": "score", "instructions": "How interesting would a typical American find this government update in their social feed?",
            "criteria": ["routine or ceremonial", "niche", "notable", "affects many people's daily lives or money", "major national news"]}


def interest(rec):
    """jev (TypeSafe's classifier, via OpenCode Zen; exe.dev's proxy adds the API key) scores 0-4."""
    body = {"model": JEV_MODEL, "questions": {"interest": INTEREST},
            "state": {"where": rec["where"], "status": rec["stage"]["short"], "headline": rec["headline"], "summary": rec["summary"]}}
    r = json.loads(fetch("https://opencode.int.exe.xyz/inference/v1/systemone", json.dumps(body).encode(),
                         {"content-type": "application/json"}))
    return round(r["answers"]["interest"]["score"], 2)


def save(new):
    """Merge an update into the bill's record; only re-run the rewrite when the source or status changed."""
    path = f"{DATA}/bills/{new['id']}.json"
    rec = load(path, {"updates": []})
    if (rec.get("source"), rec.get("stage")) != (new["source"], new["stage"]):
        rec.update(rewrite(new))
    rec.update(new)
    rec["interest"] = interest(rec)
    u = {"date": new["date"], "short": new["stage"]["short"], "now": new["stage"]["now"]}
    if u not in rec["updates"]:
        rec["updates"].append(u)
    write(path, json.dumps(rec, indent=1))
    return rec


# --- Website ----------------------------------------------------------------

e = html.escape
STEPS = ["Proposed", "Passed House or Senate", "Passed both", "Became law"]
CSS = """
:root{--ink:#1a1a1a;--muted:#5b5b5b;--line:#e4e1da;--bg:#fbfaf7;--accent:#1d4ed8;--done:#15803d}
*{box-sizing:border-box}
body{margin:0;font:18px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:var(--bg)}
header,main,footer{max-width:46rem;margin:0 auto;padding:0 1.25rem}
header{padding-top:1.25rem;display:flex;gap:1.25rem;align-items:baseline;flex-wrap:wrap}
header nav a{color:var(--ink);text-decoration:none;font-weight:600}header nav{display:flex;gap:1rem}
.brand{font-weight:800;font-size:1.25rem;color:var(--ink);text-decoration:none;margin-right:auto}.brand span{color:var(--accent)}
h1{font-size:2rem;line-height:1.2;margin:.25rem 0 1rem}
h2{font-size:1.1rem;margin:2.25rem 0 .5rem}
a{color:var(--accent)}
.eyebrow{color:var(--muted);font-size:.9rem;margin:2rem 0 0}.eyebrow a{color:var(--muted)}
.lede{font-size:1.2rem}
.box{background:#fff;border:1px solid var(--line);border-radius:12px;padding:1rem 1.25rem}
.box p{margin:.5rem 0}
.tracker{list-style:none;padding:0;margin:0 0 1rem;display:grid;grid-template-columns:repeat(4,1fr);gap:.5rem}
.tracker li{font-size:.85rem;line-height:1.3;color:var(--muted);border-top:6px solid var(--line);padding-top:.4rem}
.tracker li.done{border-color:var(--done);color:var(--ink)}
.tracker li[aria-current]{font-weight:700}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.35rem 1rem;margin:0}
dt{color:var(--muted)}dd{margin:0}
.list{list-style:none;padding:0;margin:0}
.list li{border-bottom:1px solid var(--line);padding:.8rem 0}
.list a{font-weight:600;text-decoration:none;color:var(--ink)}.list a:hover{text-decoration:underline}
.meta{display:block;color:var(--muted);font-size:.85rem}
.more{display:inline-block;margin-top:.75rem;font-weight:600}
.grid{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(13rem,1fr));gap:.5rem}
.grid a{display:block;background:#fff;border:1px solid var(--line);border-radius:10px;padding:.6rem .8rem;text-decoration:none;color:var(--ink);font-weight:600}
.grid a:hover{border-color:var(--accent)}
.official{display:inline-block;margin-top:2rem;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:.9rem}th,td{text-align:left;padding:.45rem .5rem;border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:600}
footer{color:var(--muted);font-size:.85rem;margin-top:3rem;padding-bottom:2rem}
footer p{border-top:1px solid var(--line);padding-top:1rem}
@media (max-width:32rem){h1{font-size:1.6rem}.tracker{grid-template-columns:1fr 1fr}}
"""


def date(d, fmt="%b %-d, %Y"):
    return time.strftime(fmt, time.strptime(d, "%Y-%m-%d")) if d else ""


def page(title, desc, body, path=""):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{SITE_URL}/{path}"><meta name="twitter:card" content="summary">
<style>{CSS}</style><script defer src="/_vercel/insights/script.js"></script></head><body>
<header><a class="brand" href="/">Plain<span>Gov</span></a><nav><a href="/us/">Congress</a><a href="/states/">States</a></nav></header>
<main>{body}</main>
<footer><p>Plain-English summaries are written with AI from official records and may leave out details.
Sources: <a href="https://www.congress.gov">Congress.gov</a> and each state legislature's website
(via <a href="https://github.com/openstates/openstates-scrapers">Open States' scrapers</a>).
<a href="/status/">Data status</a> · <a href="https://github.com/redreceipt/plaingov">Code</a>.</p></footer>
</body></html>
"""


def item(r, where=True):
    return (f"<li><a href=\"/{r['id']}/\">{e(r['headline'])}</a><span class=meta>"
            f"{e(r['where']) + ' · ' if where else ''}{e(r['label'])} · {e(r['stage']['short'])} · {date(r['date'])}</span></li>")


def items(recs, where=True):
    return "<ul class=list>" + "".join(item(r, where) for r in recs) + "</ul>"


def newest(recs, n):
    return sorted(recs, key=lambda r: r["date"], reverse=True)[:n]


def by_stage(recs, where):
    """List page body: newest first within plain-language stage groups."""
    groups = [("Became law", lambda s: s["step"] == 3), ("Vetoed", lambda s: "eto" in s["short"]),
              ("Passed both chambers", lambda s: s["step"] == 2), ("Passed the House or Senate", lambda s: s["step"] == 1),
              ("Just proposed", lambda s: s["step"] == 0)]
    out, left = "", recs
    for name, match in groups:
        hit = [r for r in left if match(r["stage"])]
        left = [r for r in left if r not in hit]
        if hit:
            out += f"<h2>{name}</h2>" + items(newest(hit, 25), where)
    return out or "<p>No updates yet. Check back soon.</p>"


def bill_page(r):
    st = r["stage"]
    if r["resolution"]:
        where = "House" if r["label"].startswith("H") else "Senate"
        track = f"<p>This is a resolution. It states the {where}'s position and doesn't become law.</p>"
    else:
        track = "<ol class=tracker>" + "".join(
            f"<li{' class=done' if i <= st['step'] else ''}{' aria-current=step' if i == st['step'] else ''}>{s}</li>"
            for i, s in enumerate(STEPS)) + "</ol>"
    nxt = f"<p><strong>What's next:</strong> {e(st['next'])}</p>" if st["next"] else ""
    sponsor = f"<dt>Proposed by</dt><dd>{e(r['sponsor'])}</dd>" if r.get("sponsor") else ""
    history = ""
    if len(r["updates"]) > 1:
        history = "<h2>History</h2><ul class=list>" + "".join(
            f"<li>{e(u['now'])}<span class=meta>{date(u['date'])}</span></li>" for u in reversed(r["updates"])) + "</ul>"
    home = f"/states/{r['state'].lower()}/" if r.get("state") else "/us/"
    host = urllib.parse.urlparse(r["official_url"]).netloc.removeprefix("www.")
    body = f"""<p class=eyebrow><a href="{home}">{e(r['where'])}</a> · {e(r['label'])}</p>
<h1>{e(r['headline'])}</h1>
<p class=lede>{e(r['summary'])}</p>
<h2>Where it stands</h2>
<div class=box>{track}<p><strong>Latest:</strong> {e(st['now'])} <span class=meta>{date(r['date'])}</span></p>{nxt}</div>
<h2>Details</h2>
<dl><dt>Bill number</dt><dd>{e(r['label'])}</dd><dt>Official name</dt><dd>{e(r['title'])}</dd>{sponsor}</dl>
{history}
<a class=official href="{e(r['official_url'])}">Read the full bill on {e(host)} →</a>"""
    return page(f"{r['headline']} · PlainGov", r["summary"], body, r["id"] + "/")


def build_site():
    recs = [json.load(open(p)) for p in glob.glob(f"{DATA}/bills/**/*.json", recursive=True)]
    for r in recs:
        write(f"{DATA}/site/{r['id']}/index.html", bill_page(r))
    us = [r for r in recs if not r.get("state")]
    states = [r for r in recs if r.get("state")]
    week = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
    top = sorted((r for r in recs if r["date"] >= week), key=lambda r: r.get("interest", 0), reverse=True)[:6]
    body = f"""<h1>What your government is doing, in plain English</h1>
<p class=lede>New laws and bills from Congress and all 50 states, explained without the jargon.</p>
{'<h2>Top stories this week</h2>' + items(top) if top else ''}
<h2>Congress</h2>{items(newest(us, 6), False)}<a class=more href="/us/">More from Congress →</a>
<h2>The states</h2>{items(newest(states, 8))}<a class=more href="/states/">All 50 states →</a>"""
    write(f"{DATA}/site/index.html", page("PlainGov", "What your government is doing, in plain English.", body))
    write(f"{DATA}/site/us/index.html", page("Congress · PlainGov", "What Congress is doing, in plain English.",
                                             "<h1>Congress</h1><p class=lede>Bills in the U.S. House and Senate, newest first.</p>" + by_stage(us, False), "us/"))
    counts = {st: sum(r["state"] == st for r in states) for st in STATES}
    grid = "".join(f"<li><a href=\"/states/{st.lower()}/\">{n}<span class=meta>{counts[st] or 'No'} recent update{'' if counts[st] == 1 else 's'}</span></a></li>"
                   for st, n in sorted(STATES.items(), key=lambda kv: kv[1]))
    write(f"{DATA}/site/states/index.html", page("The states · PlainGov", "What all 50 state legislatures are doing, in plain English.",
                                                 f"<h1>The states</h1><p class=lede>Pick a state to see what its lawmakers passed.</p><ul class=grid>{grid}</ul>", "states/"))
    for st, n in STATES.items():
        write(f"{DATA}/site/states/{st.lower()}/index.html", page(f"{n} · PlainGov", f"What the {n} legislature is doing, in plain English.",
              f"<p class=eyebrow><a href=\"/states/\">The states</a></p><h1>{n}</h1><p class=lede>Bills that passed a vote in the {n} legislature, newest first.</p>"
              + by_stage([r for r in states if r["state"] == st], False), f"states/{st.lower()}/"))
    write(f"{DATA}/site/status/index.html", page("Data status · PlainGov", "How fresh PlainGov's data is for each legislature.", status_body(load(HEALTH, {})), "status/"))


def status_body(health):
    label = {"ok": "Up to date", "running": "Checking now", "failed": "Having trouble", "needs setup": "Coming soon"}
    rows = "".join(
        f"<tr><td><a href=\"/states/{st.lower()}/\">{n}</a></td><td>{label.get(h.get('status'), 'Not checked yet')}</td>"
        f"<td>{time.strftime('%b %-d, %-I:%M %p UTC', time.gmtime(h['last_ok'])) if h.get('last_ok') else '—'}</td><td>{h.get('bills', '—')}</td></tr>"
        for st, n, h in sorted(((st, n, health.get(st, {})) for st, n in STATES.items()), key=lambda x: x[1]))
    return ("<h1>Data status</h1><p class=lede>We check every state legislature's website about once a day. "
            "If a site is down or changes, we get an alert and fix it.</p>"
            f"<table><thead><tr><th>State</th><th>Status</th><th>Last good check</th><th>Bills tracked</th></tr></thead><tbody>{rows}</tbody></table>")


# --- X ----------------------------------------------------------------------

def tweet_text(r):
    """Milestones (passed a chamber or further) link to the bill page; X charges much more for posts with links."""
    head = f"{r['where']}: {r['headline']}"
    tail = f"{r['label']} · {r['stage']['short']} ({date(r['date'], '%b %-d')})"
    url = f"{SITE_URL}/{r['id']}/" if r["milestone"] else ""
    room = 280 - len(head) - len(tail) - 4 - (URL_LEN + 1 if url else 0)  # 4 = blank-line separators
    body = r["tweet"]
    if len(body) > room:  # "…" weighs 2 on X, hence room - 2
        body = body[: room - 2].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return f"{head}\n\n{body}\n\n{tail}" + (f"\n{url}" if url else "")


def post_x(text):
    """POST /2/tweets as @plaingov with OAuth 1.0a user context (JSON bodies are not part of the signature)."""
    env = lambda k: os.environ[f"X_{k}"]
    url = "https://api.x.com/2/tweets"
    q = lambda s: urllib.parse.quote(s, safe="")
    oauth = {"oauth_consumer_key": env("API_KEY"), "oauth_nonce": secrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
             "oauth_token": env("ACCESS_TOKEN"), "oauth_version": "1.0"}
    base = "&".join(["POST", q(url), q("&".join(f"{q(k)}={q(v)}" for k, v in sorted(oauth.items())))])
    key = f"{q(env('API_SECRET'))}&{q(env('ACCESS_SECRET'))}"
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    auth = "OAuth " + ", ".join(f'{q(k)}="{q(v)}"' for k, v in sorted(oauth.items()))
    return json.loads(fetch(url, json.dumps({"text": text}).encode(), {"Authorization": auth, "Content-Type": "application/json"}))


def post():
    with queue_lock():
        post_best()


def post_best():
    """Post the most interesting update from the last day, spacing posts evenly under MAX_POSTS_PER_DAY."""
    qpath, lpath = f"{DATA}/state/queue.json", f"{DATA}/state/posts.json"
    now = time.time()
    queue = [q for q in load(qpath, []) if now - q["at"] < 86400]  # day-old news isn't news
    log = load(lpath, [])
    recent = [p for p in log if now - p["at"] < 86400]
    best = max(queue, key=lambda q: q["interest"], default=None)
    if not best or best["interest"] < MIN_INTEREST:
        why = "nothing interesting enough"
    elif len(recent) >= MAX_POSTS_PER_DAY or (recent and now - recent[-1]["at"] < 86400 / MAX_POSTS_PER_DAY - 300):
        why = "waiting to spread posts out"
    else:
        rec = load(f"{DATA}/bills/{best['id']}.json", None)
        text = tweet_text(rec)
        tid = "dry-run" if DRY_RUN else post_x(text)["data"]["id"]
        print(f"--- {'DRY RUN ' if DRY_RUN else ''}post {best['id']} (interest {best['interest']})\n{text}", flush=True)
        log.append({"at": now, "id": best["id"], "tweet": tid})
        write(lpath, json.dumps(log[-500:]))
        queue = [q for q in queue if q["id"] != best["id"]]
        why = "posted"
    write(qpath, json.dumps(queue))
    print(f"post: {why}; {len(queue)} queued, {len(recent)} posted in the last day")


@contextlib.contextmanager
def queue_lock():
    """The scraper daemon and the hourly poster both edit the post queue."""
    os.makedirs(f"{DATA}/state", exist_ok=True)
    with open(f"{DATA}/state/.queue.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def process(feed, items, update):
    """Build pages for a feed's new items and queue them for posting. Returns (new, updated, failed)."""
    path, qpath = f"{DATA}/state/{feed}.json", f"{DATA}/state/queue.json"
    first = not os.path.exists(path)
    seen = set(load(path, []))
    new = [(k, v) for k, v in dict(items).items() if k not in seen]
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - BACKFILL_DAYS * 86400))
    todo = [(k, v) for k, v in new if not first or v.get("_date", "9999") >= cutoff]

    def one(kv):
        try:
            return kv[0], save(update(kv[1]))
        except Exception as ex:  # retried next run; one bad item shouldn't block the rest
            print(f"FAILED {kv[0]}: {ex!r}", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(6) as pool:
        done = [d for d in pool.map(one, todo) if d]
    seen |= {k for k, _ in (new if first else done)}  # a first run never retries or posts the backlog
    write(path, json.dumps(sorted(seen)))
    if not first:
        with queue_lock():
            queue = load(qpath, []) + [{"id": r["id"], "key": k, "interest": r["interest"], "at": time.time()} for k, r in done]
            write(qpath, json.dumps(queue))
    with lock:
        build_site()
    failed = len(todo) - len(done)
    print(f"{feed}: {len(new)} new, {len(done)} pages updated, {failed} failed{' (first run: no posts queued)' if first else ''}", flush=True)
    return len(new), len(done), failed


if __name__ == "__main__":
    cmd, arg = sys.argv[1], (sys.argv[2:] or [None])[0]
    if cmd == "congress":
        sys.exit(1 if process("congress", congress_items(), congress_update)[2] else 0)
    {"site": build_site, "post": post, "scrape": lambda: scrape(arg)}[cmd]()
