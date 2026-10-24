#!/usr/bin/env python3
"""PlainGov: what your government is doing, in plain English.

Usage: plaingov.py congress   new Congress.gov bill summaries -> pages + post queue
       plaingov.py scrape     daemon: run each state legislature's scraper about daily -> pages + post queue + alerts
       plaingov.py post       post the most interesting queued update to X (@plaingov)
       plaingov.py site       rebuild the site only
       plaingov.py refresh    rewrite existing pages from before the current prompt (PROMPT_VERSION)
       plaingov.py report     X engagement for the last 7 days (the hourly post run sends it weekly)

A feed's first run builds pages for what's already listed (states: last BACKFILL_DAYS) and queues no posts.
DRY_RUN=1 (default) prints posts instead of sending them.
"""
import base64, contextlib, datetime, fcntl, glob, hashlib, hmac, html, json, os, re, secrets, shutil, statistics, subprocess, sys, threading, time, urllib.parse, urllib.request, zoneinfo
from concurrent.futures import ThreadPoolExecutor

DATA = os.environ.get("DATA_DIR", os.path.expanduser("~/plaingov-data"))
SITE_URL = os.environ.get("SITE_URL", "https://plaingov.com").rstrip("/")
DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-5-5")
STAGE_MODEL = os.environ.get("STAGE_MODEL", "gpt-6-luna")  # reads bill histories (ai_stages): 38/38 on a hand-checked set, ~$0.0002 a bill
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-1.13-free")
MAX_POSTS_PER_DAY = int(os.environ.get("MAX_POSTS_PER_DAY", "6"))
MIN_INTEREST = float(os.environ.get("MIN_INTEREST", "1.5"))  # jev score, 0-4
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "60"))
SCRAPER_IMAGE = os.environ.get("SCRAPER_IMAGE", "openstates/scrapers:latest")
PARALLEL = int(os.environ.get("PARALLEL", "12"))  # scrapers are network-bound (~85 MB, ~1% CPU each)
RESCRAPE_HOURS = float(os.environ.get("RESCRAPE_HOURS", "20"))
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "micneeley14@gmail.com")  # exe.dev only emails the VM owner
UA = "PlainGov/1.0 (+https://plaingov.com/about/)"  # some legislatures (PA, MI) reject user agents containing "bot"
URL_LEN = 23  # X counts every link as 23 chars


def fetch(url, data=None, headers={}):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def strip(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", s))).split())


def doc_text(url):
    """A bill version's text. PDFs go through pdftotext (poppler-utils); in HTML, changed text is marked
    [added: ...] / [removed: ...] so the rewrite can tell what's new."""
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=120) as r:
        return doc_bytes_text(r.read(), r.headers.get_content_charset())


def doc_bytes_text(data, charset=None):
    if data[:5] == b"%PDF-":
        return " ".join(subprocess.run(["pdftotext", "-", "-"], input=data, capture_output=True, timeout=120, check=True).stdout.decode("utf-8", "replace").split())
    mark = lambda tag: lambda m: f" [{tag}: {m[2]}] " if strip(m[2]) else " "
    t = re.sub(r"(?s)<(s|strike|del)\b[^>]*>(.*?)</\1>", mark("removed"), data.decode(charset or "utf-8", "replace"), flags=re.I)
    return strip(re.sub(r"(?s)<(u|ins)\b[^>]*>(.*?)</\1>", mark("added"), t, flags=re.I))


SOURCE_CHARS = 40000  # shortcut: longer bills (budgets) get explained from their first ~10k words


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
    """Stage from a Congress.gov action, like "Passed/agreed to in Senate: ..." or "Became Public Law No: 119-45."."""
    ch = "House" if "House" in desc else "Senate"
    other = "Senate" if ch == "House" else "House"
    if "Public Law" in desc:
        return stage(3, "It's law", "It's officially law now.")
    if re.search(r"\bvetoed\b", desc, re.I):
        return stage(2, "Vetoed", "The President said no (that's a veto).", "Congress can still make it law if two-thirds of the House and Senate vote to overrule the President.")
    if re.search(r"Presented to President|Resolving differences|agreed? to (the )?(House|Senate) amendment", desc):
        return stage(2, "Passed Congress", "The House and Senate both said yes.", "Now it's up to the President to sign it or not.")
    if desc.startswith("Passed"):
        return stage(1, f"Passed the {ch}", f"The {ch} voted yes.", f"Now the {other} has to say yes too.")
    if desc.startswith("Reported"):
        return stage(0, f"Moving in the {ch}", f"A small group in the {ch} (a committee) looked it over and sent it on.", f"The whole {ch} might vote on it next.")
    who = "Someone in the House" if ch == "House" else "A senator"
    return stage(0, "Proposed", f"{who} proposed it.", f"A small group in the {ch} (a committee) looks at it first. Heads up: most bills never make it past this step.")


def sponsor_name(full):
    """'Sen. Hassan, Margaret Wood [D-NH]' -> 'Sen. Margaret Wood Hassan (D-NH)'"""
    m = re.match(r"(\S+) ([^,]+), (.+?) \[(\w+)-(\w+)", full or "")
    return f"{m[1]} {m[3]} {m[2]} ({m[4]}-{m[5]})" if m else full


def congress_items(days=3):
    """Bills whose latest official action happened in the last week, from bills Congress.gov updated in the last `days`,
    oldest first. CRS summaries usually arrive months after the action, so they're source material, not news."""
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))
    week = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
    found, offset = [], 0
    while True:
        batch = api("bill", limit=250, offset=offset, sort="updateDate desc", fromDateTime=since)["bills"]
        found += batch
        if len(batch) < 250:
            break
        offset += 250
    for b in reversed(found):
        a = b.get("latestAction") or {}
        if b["type"] in TYPES and a.get("actionDate", "") >= week:
            yield f"{b['congress']}-{b['type']}-{b['number']}-{us_stage(a['text'])['short']}", {**b, "_date": a["actionDate"]}


def congress_update(b):
    t, a = b["type"], b["latestAction"]
    label, slug = TYPES[t]
    st = us_stage(a["text"])
    if t in RESOLUTIONS and st["step"] > 0:
        st["next"] = ""
    path = f"bill/{b['congress']}/{t.lower()}/{b['number']}"
    sponsors = api(path)["bill"].get("sponsors") or [{}]
    summaries = api(f"{path}/summaries").get("summaries") or []
    crs = strip(max(summaries, key=lambda x: x.get("updateDate", ""))["text"]) if summaries else ""
    text = ""
    try:  # the bill's own text, besides CRS's summary when there is one
        url = next((f["url"] for v in api(f"{path}/text").get("textVersions", []) for f in v["formats"] if f["type"] == "Formatted Text"), None)
        text = doc_text(url) if url else ""
    except Exception as ex:
        print(f"no text for {path}: {ex!r}", file=sys.stderr, flush=True)
    return {
        "id": f"us/{b['congress']}-{t.lower()}-{b['number']}", "where": "U.S. Congress", "state": None,
        "label": f"{label} {b['number']}", "resolution": t in RESOLUTIONS, "title": b["title"],
        "official_url": f"https://www.congress.gov/bill/{ordinal(b['congress'])}-congress/{slug}/{b['number']}",
        "sponsor": sponsor_name(sponsors[0].get("fullName")), "date": a["actionDate"], "stage": st,
        "milestone": st["step"] >= 1, "source": " ".join(x for x in (b["title"], crs, text) if x)[:SOURCE_CHARS],
        "actions": [f"{a['actionDate']}: {a['text']}"],
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
# California's image bundles MySQL, loads the state's database dumps, then scrapes; its entrypoint supplies "ca".
CA_IMAGE = os.environ.get("CA_SCRAPER_IMAGE", "openstates/scrapers-california:latest")
LAW = {"became-law", "executive-signature", "veto-override-passage"}
BILLS = {"bill", "joint resolution", "constitutional amendment"}  # resolutions (commemorations, positions) never become law
RPM = {"UT": 20, "WI": 20}  # requests/minute for sites that rate-limit the default 60
# Sites that block automated visitors (IP blocks, Cloudflare challenges): use LegiScan's API there instead (CC BY 4.0).
LEGISCAN_STATES = [s for s in os.environ.get("LEGISCAN_STATES", "IL,AK,NH").split(",") if s]  # NH drops connections from our VM
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
        2: stage(1, f"Passed the {ch}", f"The {ch} voted yes.", f"Now the {other} has to say yes too."),
        3: stage(2, "Passed lawmakers", "State lawmakers said yes.", "Now it's up to the Governor to sign it or not."),
        4: stage(3, "It's law", "It's officially law now."),
        5: stage(2, "Vetoed", "The Governor said no (that's a veto).", "Lawmakers can still make it law if enough of them vote to overrule the Governor."),
    }[status]


def scraped(st):
    return glob.glob(f"{DATA}/scrape/{st.lower()}/**/bill_*.json", recursive=True)  # scrapers write <datadir>/<state>/


def stage_of(b, ai):
    """The classified actions' stage, unless the AI's reading of the whole history (ai_stages) says otherwise."""
    det, key = bill_stage(b), f"{b['legislative_session']}|{b['identifier']}"
    if key not in ai or (ai[key] and ai[key][0]) == (det and det[0]):
        return det
    return tuple(ai[key]) if ai[key] else None


def state_items(st, ai={}):
    """Milestones in a state's latest scrape."""
    for f in scraped(st):
        b = json.load(open(f))
        s = stage_of(b, ai) if set(b["classification"]) & BILLS else None
        if s:
            yield f"{st}-{b['legislative_session']}-{b['identifier']}-{s[0]}", {**b, "state": st, "status": s, "_date": s[2]}


forbidden = {}  # host -> when it answered 403 (CA's leginfo blocks every non-browser client); skipped for a day


def bill_text(b):
    """Text of the newest version we can read (HTML first, then PDF), for richer rewrites."""
    for v in sorted(b["versions"], key=lambda v: v.get("date") or "", reverse=True):
        for link in sorted(v["links"], key=lambda l: l.get("media_type") != "text/html"):
            host = urllib.parse.urlparse(link["url"]).netloc
            if link.get("media_type") in ("text/html", "application/pdf") and time.time() - forbidden.get(host, 0) > 86400:
                try:
                    if len(text := doc_text(link["url"])) > 200:
                        return text
                except Exception as ex:
                    if getattr(ex, "code", None) == 403:
                        forbidden[host] = time.time()
                    print(f"bill text {link['url']}: {ex!r}", file=sys.stderr, flush=True)
    return ""


def last_actions(acts):
    """The latest few official actions, oldest first, as 'YYYY-MM-DD: what happened'."""
    return [f"{a['date'][:10]}: {a['description']}" for a in sorted(acts, key=lambda a: a["date"])[-6:]]


def state_id(b):
    return f"states/{b['state'].lower()}/{slug(b['legislative_session'])}-{slug(b['identifier'])}"


def state_update(b):
    st, (status, chamber, day) = b["state"], b["status"]
    people = [s for s in b["sponsorships"] if s["primary"]] or b["sponsorships"]
    sponsor = None
    if people:
        lower = "Asm. " if st in ASSEMBLY else "Rep. "
        title = ("Sen. " if org(b["from_organization"]) in ("upper", "legislature") else lower) if people[0].get("entity_type") == "person" else ""
        more = len(people) - 1
        sponsor = title + people[0]["name"] + (f" and {more} other{'s' if more > 1 else ''}" if more else "")
    abstract = " ".join(a["abstract"] for a in b["abstracts"])
    source = " ".join(x for x in (b["title"], abstract, bill_text(b)) if x)[:SOURCE_CHARS]
    return {
        "id": state_id(b), "where": STATES[st], "state": st, "actions": last_actions(b.get("actions", [])),
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


def bill_num(n):
    """'HB 0012' -> 'HB12', so LegiScan and scraper bill numbers match."""
    return re.sub(r"^([A-Z]+)0*(?=\d)", r"\1", re.sub(r"[^A-Z0-9]", "", n.upper()))


def save_ids(st, ai={}):
    """Bill number -> [session, identifier, status] from a good scrape's main session, for a LegiScan backup later."""
    rows = []
    for f in scraped(st):
        b = json.load(open(f))
        if set(b["classification"]) & BILLS:
            s = stage_of(b, ai)
            rows.append((b["legislative_session"], b["identifier"], s[0] if s else 0, max((a["date"][:4] for a in b["actions"]), default="0")))
    # shortcut: only the session with the most bills; special-session bills (numbers can repeat) aren't backed up
    sessions = [r[0] for r in rows]
    main = max(set(sessions), key=sessions.count, default=None)
    write(f"{DATA}/state/ids-{st.lower()}.json", json.dumps({"at": time.strftime("%Y-%m-%d", time.gmtime()), "year": int(max((r[3] for r in rows if r[0] == main), default=0)),
          "bills": {bill_num(i): [s, i, n] for s, i, n, _ in rows if s == main}}))


def legiscan_items(st, known=None):
    """Milestones from LegiScan's bill list for a state: 2 passed one chamber, 3 passed both, 4 law, 5 vetoed.
    As a backup for a broken scraper, `known` (save_ids) maps them onto the scraper's bill IDs and post keys, so a bill
    never gets two pages or posts. Only milestones since that scrape count (scrapers can miss old ones, e.g. SC's "Act No.":
    that's not news); bills the scraper didn't know, or already had at that stage, are skipped."""
    q = urllib.parse.urlencode({"key": os.environ["LEGISCAN_API_KEY"], "op": "getMasterList", "state": st})
    r = json.loads(fetch(f"https://api.legiscan.com/?{q}"))
    if r.get("status") != "OK":
        raise RuntimeError(f"LegiScan: {r.get('alert', r)}")
    session = r["masterlist"].get("session", {})
    if known and (session.get("special") or session.get("year_start", 0) > known["year"]):  # bill numbers restart each session
        raise RuntimeError(f"LegiScan is on {session.get('session_name')}, not the session the scraper last saw")
    bills = [b for b in r["masterlist"].values() if isinstance(b, dict) and "bill_id" in b]
    for b in bills:
        s = int(b.get("status") or 0)
        if s in (2, 3, 4, 5) and not re.match(r"^[HSA]C?R\d", b["number"]):  # HR, SR, HCR...: resolutions never become law
            chamber = "upper" if b["number"].startswith("S") else "lower"
            item = {**b, "state": st, "status": (s, chamber, b["status_date"]), "_date": b["status_date"]}
            if known is None:
                yield f"{st}-ls{b['bill_id']}-{s}", item
            elif (k := known["bills"].get(bill_num(b["number"]))) and s > k[2] and b["status_date"] >= known["at"]:
                yield f"{st}-{k[0]}-{k[1]}-{s}", {**item, "_session": k[0], "number": k[1]}


def legiscan_id(b):
    return f"states/{b['state'].lower()}/{slug(b.get('_session') or b['url'].rstrip('/').rsplit('/', 1)[-1])}-{slug(b['number'])}"


def legiscan_update(b):
    """No sponsor in LegiScan's bill list; leaving it out keeps the one a scraper found. getBill (one query) adds the
    official history and the newest text, read from the legislature's own copy."""
    st, (status, chamber, day), num = b["state"], b["status"], b["number"]
    desc = b.get("description") or ""
    actions, text = [f"{b['last_action_date']}: {b['last_action']}"] if b.get("last_action") else [], ""
    if b.get("bill_id"):
        q = urllib.parse.urlencode({"key": os.environ["LEGISCAN_API_KEY"], "op": "getBill", "id": b["bill_id"]})
        bill = json.loads(fetch(f"https://api.legiscan.com/?{q}")).get("bill", {})
        actions = [f"{h['date']}: {h['action']}" for h in bill.get("history", [])[-6:]] or actions
        for t in reversed(bill.get("texts", [])):
            try:
                if len(text := doc_text(t["state_link"])) > 200:
                    break
            except Exception as ex:  # e.g. a legislature's broken certificate chain: use LegiScan's copy (one more query)
                print(f"bill text {t.get('state_link')}: {ex!r}", file=sys.stderr, flush=True)
                try:
                    q = urllib.parse.urlencode({"key": os.environ["LEGISCAN_API_KEY"], "op": "getBillText", "id": t["doc_id"]})
                    if len(text := doc_bytes_text(base64.b64decode(json.loads(fetch(f"https://api.legiscan.com/?{q}"))["text"]["doc"]))) > 200:
                        break
                except Exception as ex2:
                    print(f"LegiScan text {t.get('doc_id')}: {ex2!r}", file=sys.stderr, flush=True)
            text = ""
    return {
        "id": legiscan_id(b), "where": STATES[st], "actions": actions,
        "state": st, "label": re.sub(r"^([A-Z]+)(\d+)$", r"\1 \2", num), "resolution": False, "title": b["title"], "official_url": b["url"],
        "date": day, "stage": state_stage(st, status, chamber), "milestone": True,
        "source": " ".join(x for x in (b["title"], "" if desc == b["title"] else desc, text) if x)[:SOURCE_CHARS],
    }


def legiscan_state(st):
    """Fallback for states whose sites block automated visitors; health and alerts work the same as scrapers."""
    prev = load(HEALTH, {}).get(st, {})
    if not os.environ.get("LEGISCAN_API_KEY"):
        return set_health(st, status="needs setup", error="site blocks automated visitors; needs LEGISCAN_API_KEY", last_start=time.time())
    set_health(st, status="running", last_start=time.time())
    try:
        found = list(legiscan_items(st))
        new, done, failed = process(f"states-{st.lower()}", found, legiscan_update)
        h = set_health(st, status="ok", error="", last_end=time.time(), last_ok=time.time(), bills=len(found), source="LegiScan",
                       milestones=new, page_failures=failed)
        if prev.get("status") == "failed":
            alert(f"PlainGov: {STATES[st]} recovered", f"{STATES[st]} updated from LegiScan again.")
    except Exception as ex:
        h = set_health(st, status="failed", error=repr(ex)[-800:], last_end=time.time())
        if prev.get("status") != "failed":
            alert(f"PlainGov: {STATES[st]} (LegiScan) failed", f"{ex!r}\n\nStatus page: {SITE_URL}/status/")
    return h


def legiscan_backup(st):
    """Fill in a broken scraper's state from LegiScan. Once a day per state at most (failed scrapers retry every 2 hours),
    so even all 50 failing at once stays far under LegiScan's 10,000 free queries a month."""
    known = load(f"{DATA}/state/ids-{st.lower()}.json", None)
    if not known:  # no good scrape to map bill IDs onto yet
        return set_health(st, backup="")
    h = set_health(st, backup_at=time.time(), backup="LegiScan")  # set first: process() rebuilds /status
    try:
        process(f"states-{st.lower()}", legiscan_items(st, known), legiscan_update)
        return h
    except Exception as ex:
        print(f"{st} LegiScan backup failed: {ex!r}", file=sys.stderr, flush=True)
        return set_health(st, backup="")


def scrape_state(st):
    """Run one state's scraper, publish its new milestones, and alert when its health changes."""
    # Blocked sites, and states still waiting on their legislature's API key, use LegiScan in the meantime.
    if st in LEGISCAN_STATES or (st in KEYS and not os.environ.get(KEYS[st])):
        return legiscan_state(st)
    name, prev = STATES[st], load(HEALTH, {}).get(st, {})
    out = f"{DATA}/scrape/{st.lower()}"
    os.makedirs(out, exist_ok=True)
    # The image only runs as root (its Python env lives in /root), so clean up and hand files back via the image too.
    in_box = lambda *cmd: subprocess.run(["docker", "run", "--rm", "-v", f"{out}:/data", "--entrypoint", cmd[0], SCRAPER_IMAGE, *cmd[1:]], capture_output=True)
    in_box("find", "/data", "-mindepth", "1", "-delete")
    set_health(st, status="running", last_start=time.time())
    box = f"plaingov-scrape-{st.lower()}"
    subprocess.run(["docker", "rm", "-f", box], capture_output=True)  # left over if the daemon restarted mid-scrape
    cmd = ["docker", "run", "--rm", "--name", box, "-v", f"{out}:/data",
           "-v", f"{os.path.dirname(os.path.abspath(__file__))}/scrapers:/plaingov:ro", "-e", "PYTHONPATH=/plaingov:./scrapers", "-e", f"USER_AGENT={UA}",  # ./scrapers: the images' own
           *[a for k in KEYS.values() if os.environ.get(k) for a in ("-e", k)],
           *([CA_IMAGE] if st == "CA" else [SCRAPER_IMAGE, st.lower()]), "bills", "--scrape", "--datadir", "/data", "--rpm", str(RPM.get(st, 60))]
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
        ai = ai_stages(st)
        new, done, failed = process(f"states-{st.lower()}", state_items(st, ai), state_update)
        save_ids(st, ai)
        stats = {"last_ok": time.time(), "bills": bills, "sessions": sessions, "milestones": new, "page_failures": failed, "backup": ""}
    h = set_health(st, status=status, error=error[-800:], last_end=time.time(), site=site, **stats)
    if status == "failed" and prev.get("status") != "failed":
        up = site_status(site)
        cause = (f"The {name} legislature's website looks {up}, so this is probably on their end." if up.startswith("down")
                 else f"The {name} legislature's website is {up}, so the scraper probably needs a fix (their site may have changed).")
        alert(f"PlainGov: {name} scraper failed", f"{cause}\n\nError:\n{error[-1500:]}\n\nStatus page: {SITE_URL}/status/\n"
              f"Rerun: ssh plaingov.exe.xyz 'cd plaingov && python3 plaingov.py scrape {st}'")
    elif status == "ok" and prev.get("status") == "failed":
        alert(f"PlainGov: {name} scraper recovered", f"{name} scraped {bills} bills successfully again.")
    if status == "failed" and os.environ.get("LEGISCAN_API_KEY") and time.time() - prev.get("backup_at", 0) > RESCRAPE_HOURS * 3600:
        h = legiscan_backup(st)
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
            for image in (SCRAPER_IMAGE, CA_IMAGE):
                subprocess.run(["docker", "pull", "-q", image], capture_output=True)
            pulled = time.time()
        for st, f in list(running.items()):
            if f.done():
                running.pop(st)
                if f.exception():
                    set_health(st, status="failed", error=repr(f.exception()), last_end=time.time())
                    alert(f"PlainGov: {STATES[st]} run crashed", repr(f.exception()))
        health = load(HEALTH, {})
        # Failures are usually a site hiccup (500s, timeouts), so retry those after 2 hours instead of a day.
        wait = lambda h: (2 if h.get("status") == "failed" else RESCRAPE_HOURS) * 3600
        due = sorted((st for st in STATES if st not in running and time.time() - health.get(st, {}).get("last_start", 0) > wait(health.get(st, {}))),
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

PROMPT_VERSION = 5  # bump to rewrite every page with a changed prompt (python3 plaingov.py refresh)
PROMPT = """You write for PlainGov, which explains government to regular people who don't follow politics. After reading your page, someone should know exactly what this {where} bill does, what it changes, and what just happened to it.

Rules:
- Use only facts in the source and official actions. Don't guess about effects, costs, or motives, and never invent details.
- Be concrete everywhere, headline included: name the actual people, places, amounts, ages, deadlines, and limits the source gives. Never write placeholders like "certain places" or "a safety device" when the source says what they are. Don't pad with definitions of general terms (don't explain what an effective date is: give the date).
- Keep legal conditions and exceptions intact: if protection only applies when conditions are met, say so instead of rounding it to "no penalty".
- Say what's new versus what already existed when the source shows it. "[added: ...]" and "[removed: ...]" mark changed text, and "is amended to read" means existing law is being changed. Don't call something new if the bill only adjusts an existing rule.
- Neutral and nonpartisan. No opinions, hype, slang, emojis, or hashtags.
- Everyday words and short sentences, about an 8th-grade reading level. Explain any legal or technical term in a few plain words, and don't cite statute numbers (like "AS 11.81.500" or "section 8-11-155").
- Action dates are when each action was recorded. If an action's text gives the real date (like "SIGNED INTO LAW 9/9"), use that one.
- Match the status. If it's law, say what it does ("Hospitals now have to...") or when it starts if that's later ("Starting July 1, 2027, ..."). If it isn't law yet, say what it would do ("would", "if it passes").
- If the source is only a title or a short description, say only what that tells you, keep it short, and set "limited" to true. Don't fill space.

Reply with only a JSON object with these keys:
"headline": what changes for people, in 4-10 everyday words, sentence case, no period (max 70 characters). Like "Hospitals would have to give you an itemized bill"
"tweet": one sentence that makes someone stop scrolling, leading with who it affects or what changes for them, in concrete terms (max 140 characters)
"summary": 2-3 short sentences: the main thing it does and who it affects (max 350 characters)
"details": a list of 2-6 short sentences with the specifics: who it covers, what's required or allowed, limits, exceptions, money, deadlines ([] if the source doesn't say)
"changes": 1-2 sentences on what's different from current law ("" if the source doesn't show it)
"effective": when it takes effect, like "September 10, 2026" or "90 days after it becomes law"; prefer a date in the official actions over one in the text ("" if neither says)
"happened": one sentence on the latest official action and its date, in plain words, like "The Governor signed it on September 9, 2026."
"limited": true if the source is only a title or short description, else false

Official name: {title}
Status: {status}
Latest official actions:
{actions}
Source: {source}"""

CHECK_PROMPT = """Check this plain-English explanation of a bill against its official source and actions. Reply with only JSON: {{"vague": true or false, "unsupported": ["..."]}}
Status: {status}
- vague: true only if it stays at the level of the topic (names the subject, or uses placeholders like "certain places" or "a safety device") when the source says what the bill actually requires, allows, or covers. Leaving out some details is fine.
- unsupported: each claim that the source or actions don't support, including overstated legal effects, and wording that doesn't match the status (a law described with "would", or a proposal described as if it were law). Plain rewording is fine. [] if none.

Official actions:
{actions}
Source: {source}

Explanation: {rewrite}"""
REWRITE_KEYS = ("headline", "tweet", "summary", "details", "changes", "effective", "happened", "limited")


def llm(prompt, max_tokens, model=LLM_MODEL):
    """exe.dev's LLM integration (it adds the API keys): (reply text, stop reason). Claude via /messages, others via /chat/completions."""
    if model.startswith("claude"):
        body = {"model": model, "max_tokens": max_tokens, "thinking": {"type": "disabled"}, "messages": [{"role": "user", "content": prompt}]}
        r = json.loads(fetch("https://llm.int.exe.xyz/v1/messages", json.dumps(body).encode(), {"content-type": "application/json", "anthropic-version": "2023-06-01"}))
        return "".join(c["text"] for c in r["content"] if c["type"] == "text"), r.get("stop_reason")
    body = {"model": model, "max_completion_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    c = json.loads(fetch("https://llm.int.exe.xyz/v1/chat/completions", json.dumps(body).encode(), {"content-type": "application/json"}))["choices"][0]
    return c["message"]["content"] or "", c.get("finish_reason")


def rewrite(rec):
    """Claude writes the page and STAGE_MODEL checks it against the source. Claude gets one retry with that feedback, then
    STAGE_MODEL (more precise, less plain) a last one. A rewrite that still fails raises, so the bill is tried again next
    run instead of publishing something vague or wrong."""
    actions = "\n".join(rec.get("actions") or [f"{rec['date']}: {rec['stage']['now']}"])
    prompt = PROMPT.format(**{**rec, "actions": actions, "status": rec["stage"]["now"] + (
        " This IS law now: use present tense, or say when it starts if that's later." if rec["stage"]["step"] == 3
        else " This is NOT law yet: say what it would do.")})
    for model in (LLM_MODEL, LLM_MODEL, STAGE_MODEL):
        out = ask_json(prompt, 6000, model)
        out = {k: [" ".join(str(x).split()) for x in (v if isinstance(v, list) else [v] if v else [])] if k == "details"
               else bool(v) if k == "limited" else " ".join(str(v).split()) for k, v in out.items() if k in REWRITE_KEYS}
        problems = []
        if re.search(r"\b(the|this) source\b|\bsource text\b|\bfull text\b|\b(text|title) (does not|doesn't)\b", out.get("summary", "") + out.get("tweet", ""), re.I):
            problems.append("It talks about the source instead of the bill.")
        if not all(out.get(k) for k in ("headline", "tweet", "summary")):
            problems.append("headline, tweet, and summary are required.")
        if not problems:
            check = ask_json(CHECK_PROMPT.format(status=rec["stage"]["now"], actions=actions, source=rec["source"], rewrite=json.dumps(out)), 6000, STAGE_MODEL)
            problems = [f"Unsupported: {c}" for c in check.get("unsupported") or []]
            if check.get("vague") and not out.get("limited"):
                problems.append("Too vague: say what the bill actually requires, allows, or covers, using the source's specifics.")
        if not problems:
            return {k: out.get(k, [] if k == "details" else "") for k in REWRITE_KEYS}
        prompt += "\n\nYour last answer had these problems. Fix them and reply with the full JSON again:\n- " + "\n- ".join(problems)
    raise ValueError(f"rewrite failed checks three times: {problems}")


STAGE_PROMPT = """Here is the official action history of {where} bill {identifier} ("{title}"), oldest first, as "date | chamber | action".
How far has this bill itself gotten? Be strict: say a milestone only if an action clearly shows it.
- Passing a chamber means that whole chamber's final vote approving this bill (usually third reading). Not: committee votes or reports, "reported", placed on a calendar or general order, first or second readings, ordered to a reading, substitutions, or failed votes.
- In a one-chamber legislature (like Nebraska's), its final passage counts as "passed both chambers".
- Passing both chambers means both approved the same final version, so it goes to the Governor. If they passed different versions and still have to agree (like a conference committee), it has passed one chamber: the first one that passed it.
- Law means this bill was signed, became law without a signature, got an act or chapter number, or had a veto overridden. Sent or delivered to the Governor isn't law yet. A line-item or partial veto doesn't stop a signed bill from being law.
- Actions about a companion bill (like "Comp. became Pub. Ch. 157" or "Companion House Bill substituted"; "Comp." means companion) are about that other bill, which has its own page: they don't move this bill forward. But when this bill is substituted for another one (like "Substituted for S4059") and then passes, that vote counts for this bill.
- A resolution adopted by both chambers is "passed both chambers", not "law".
- Vetoed means the Governor rejected the whole bill and lawmakers didn't override it (including when an override vote failed or the veto was sustained). A veto stays "vetoed" while an override is pending or possible (like "Consideration of Governor's veto pending"). A conditional veto that lawmakers then accepted counts as "passed both chambers" again.
Reply with only JSON with all three keys, like {{"status": "passed one chamber", "chamber": "upper", "date": "2026-03-05"}}. status is "none", "passed one chamber", "passed both chambers", "law", or "vetoed"; chamber is "upper" or "lower" for "passed one chamber", otherwise ""; date is the YYYY-MM-DD of the action that got it there.

{lines}"""
STATUSES = {"passed one chamber": 2, "passed both chambers": 3, "law": 4, "vetoed": 5}


def ask_json(prompt, max_tokens, model=LLM_MODEL):
    """The first JSON object in the reply (models sometimes add a note after)."""
    text, stop = llm(prompt, max_tokens, model)
    if "{" not in text:
        raise ValueError(f"no JSON from LLM (stop_reason={stop}): {text[:200]!r}")
    return json.JSONDecoder().raw_decode(text[text.index("{"):])[0]


def judge_stage(st, b):
    """STAGE_MODEL reads a bill's whole history: [status, chamber, date], or None if no milestone."""
    acts = sorted(b["actions"], key=lambda a: a["date"])[-80:]
    out = ask_json(STAGE_PROMPT.format(where=STATES[st], identifier=b["identifier"], title=b["title"][:300], lines="\n".join(
        f"{a['date'][:10]} | {org(a.get('organization_id')) or '-'} | {a['description'][:300]}" for a in acts)), 2000, STAGE_MODEL)
    status, day = STATUSES.get(out.get("status")), str(out.get("date", ""))
    if out.get("status") == "none":
        return None
    if not status or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not acts[0]["date"][:10] <= day <= acts[-1]["date"][:10]:
        raise ValueError(f"unclear answer: {out}")  # not "none": keep the classified stage, ask again next scrape
    return [status, out.get("chamber") if status == 2 and out.get("chamber") in ("upper", "lower") else None, day]


def ai_stages(st):
    """State sites word things their own way, and sites change, so scrapers miss milestones (SC "Act No. 245",
    NE "Returned by Governor without approval") and mislabel others (HI's "Passed Second Reading" as passage).
    STAGE_MODEL reads the history of each bill short of law with a classified milestone or unclassified actions, if it
    moved in the last BACKFILL_DAYS (older bills aren't news, and checking whole sessions costs ~30x more). Laws are
    trusted. Cached until a bill's actions, the prompt, or the model change. Returns {"session|identifier": [status, chamber, date] or None}."""
    path = f"{DATA}/state/stages-{st.lower()}.json"
    cache = load(path, {})
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - BACKFILL_DAYS * 86400))
    todo = []
    for f in scraped(st):
        b = json.load(open(f))
        if not set(b["classification"]) & BILLS or max((a["date"][:10] for a in b["actions"]), default="") < since:
            continue
        det = bill_stage(b)
        if (det and det[0] != 4) or (not det and any(not a["classification"] for a in b["actions"])):
            # the prompt and model are part of the hash, so improving either re-checks every bill
            key, h = f"{b['legislative_session']}|{b['identifier']}", hashlib.sha1((json.dumps(b["actions"], sort_keys=True) + STAGE_PROMPT + STAGE_MODEL).encode()).hexdigest()[:12]
            if cache.get(key, {}).get("h") != h:
                todo.append((key, h, b))

    def one(t):
        try:
            return t[0], {"h": t[1], "s": judge_stage(st, t[2])}
        except Exception as ex:  # asked again next scrape
            print(f"{st} {t[0]}: stage check failed: {ex!r}", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(6) as pool:
        cache.update(d for d in pool.map(one, todo) if d)
    write(path, json.dumps(cache, indent=0))
    return {k: v["s"] for k, v in cache.items()}


INTEREST = {"type": "score", "instructions": "How interesting would a typical American find this government update in their social feed?",
            "criteria": ["routine or ceremonial", "niche", "notable", "affects many people's daily lives or money", "major national news"]}


def interest(rec):
    """jev (TypeSafe's classifier, via OpenCode Zen; exe.dev's proxy adds the API key) scores 0-4."""
    body = {"model": JEV_MODEL, "questions": {"interest": INTEREST},
            "state": {"where": rec["where"], "status": rec["stage"]["short"], "headline": rec["headline"], "summary": rec["summary"]}}
    try:
        r = json.loads(fetch("https://opencode.int.exe.xyz/inference/v1/systemone", json.dumps(body).encode(),
                             {"content-type": "application/json"}))
        return round(r["answers"]["interest"]["score"], 2)
    except urllib.error.HTTPError as ex:  # jev's free tier has a daily limit; Claude keeps posting going until it resets
        if ex.code not in (402, 429):
            raise
    prompt = (f"{INTEREST['instructions']} Score 0-4: " + "; ".join(f"{i}={c}" for i, c in enumerate(INTEREST["criteria"]))
              + f". Reply with only the number.\n\n{json.dumps(body['state'])}")
    return min(4.0, max(0.0, float(re.search(r"\d+(\.\d+)?", llm(prompt, 10)[0]).group(0))))


def score(rec):
    """Store a jev interest score on the bill; None (try again later) if jev is unavailable, e.g. its free daily limit."""
    try:
        rec["interest"] = interest(rec)
        write(f"{DATA}/bills/{rec['id']}.json", json.dumps(rec, indent=1))
    except Exception as ex:
        print(f"jev unavailable for {rec.get('id')}: {ex!r}", file=sys.stderr, flush=True)
        rec["interest"] = None
    return rec["interest"]


def save(new):
    """Merge an update into the bill's record; only re-run the rewrite when the source or status changed."""
    path = f"{DATA}/bills/{new['id']}.json"
    rec = load(path, {"updates": []})
    if (rec.get("source"), rec.get("stage"), rec.get("v")) != (new["source"], new["stage"], PROMPT_VERSION):
        rec.update(rewrite(new), v=PROMPT_VERSION)
    rec.update(new)
    u = {"date": new["date"], "short": new["stage"]["short"], "now": new["stage"]["now"]}
    if u not in rec["updates"]:
        rec["updates"].append(u)
    write(path, json.dumps(rec, indent=1))
    return rec


# --- Website ----------------------------------------------------------------

e = html.escape
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")  # logo, icons, social card (brand/build.py)
MARK = open(os.path.join(STATIC, "favicon.svg")).read().replace("<svg ", '<svg aria-hidden="true" width="28" height="28" ', 1)
STEPS = ["Proposed", "Halfway there", "Passed House & Senate", "It's law"]
CSS = """
:root{--ink:#111827;--muted:#4b5563;--soft:#6b7280;--line:#e5e2db;--bg:#fbfaf7;--accent:#1d4ed8;--done:#15803d}
*{box-sizing:border-box}
body{margin:0;font:18px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;color:var(--ink);background:var(--bg);-webkit-text-size-adjust:100%}
.wrap{max-width:46rem;margin:0 auto;padding:0 1.25rem}
a{color:var(--accent)}
a:focus-visible{outline:3px solid var(--accent);outline-offset:2px;border-radius:4px}
.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:#fff;padding:.5rem 1rem;z-index:9}
.top{background:#fff;border-bottom:1px solid var(--line)}
.top .wrap{display:flex;align-items:center;gap:1rem;height:3.75rem}
.brand{display:inline-flex;align-items:center;gap:.5rem;font-weight:800;font-size:1.2rem;letter-spacing:-.01em;color:var(--ink);text-decoration:none}
.brand b{color:var(--accent);font-weight:800}
.top .brand{margin-right:auto}
.top nav{display:flex;gap:1.1rem}.top nav a{color:var(--ink);text-decoration:none;font-weight:600;font-size:.95rem}.top nav a:hover{color:var(--accent)}
.hero{padding:1.5rem 0 .25rem}.hero h1{font-size:2.4rem}
h1{font-size:2rem;line-height:1.15;letter-spacing:-.015em;margin:1.75rem 0 .75rem}
h2{font-size:1.15rem;margin:2.25rem 0 .5rem}
.eyebrow{color:var(--soft);font-size:.9rem;margin:1.75rem 0 0}.eyebrow a{color:var(--soft)}
.eyebrow+h1{margin-top:.25rem}
.lede{font-size:1.2rem;color:#1f2937}
.box{background:#fff;border:1px solid var(--line);border-radius:12px;padding:1rem 1.25rem}
.box p{margin:.5rem 0}
.tracker{list-style:none;padding:0;margin:0 0 1rem;display:grid;grid-template-columns:repeat(4,1fr);gap:.5rem}
.tracker li{font-size:.85rem;line-height:1.3;color:var(--soft);border-top:6px solid var(--line);padding-top:.4rem}
.tracker li.done{border-color:var(--done);color:var(--ink)}
.tracker li[aria-current]{font-weight:700}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.35rem 1rem;margin:0}
dt{color:var(--soft)}dd{margin:0}
.list{list-style:none;padding:0;margin:0}
.list li{border-bottom:1px solid var(--line);padding:.8rem 0}
.list a{font-weight:600;text-decoration:none;color:var(--ink)}.list a:hover{text-decoration:underline}
.meta{display:block;color:var(--soft);font-size:.85rem}
.more{display:inline-block;margin-top:.75rem;font-weight:600}
.grid{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(13rem,1fr));gap:.5rem}
.grid a{display:block;background:#fff;border:1px solid var(--line);border-radius:10px;padding:.6rem .8rem;text-decoration:none;color:var(--ink);font-weight:600}
.grid a:hover{border-color:var(--accent)}
.official{display:inline-block;margin-top:2rem;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:.9rem}th,td{text-align:left;padding:.45rem .5rem;border-bottom:1px solid var(--line)}th{color:var(--soft);font-weight:600}
.site-foot{margin-top:3.5rem;padding-top:1.5rem;padding-bottom:2.5rem;border-top:1px solid var(--line);color:var(--muted);font-size:.9rem}
.foot{display:flex;flex-wrap:wrap;gap:1.25rem 2rem;justify-content:space-between}
.foot p{margin:.4rem 0 0}
.foot nav{display:flex;flex-wrap:wrap;gap:.4rem 1.1rem;max-width:25rem}.foot nav a{color:var(--muted)}
.fine{font-size:.8rem;color:var(--soft);margin-top:1.25rem}
@media (max-width:32rem){.hero h1{font-size:1.9rem}h1{font-size:1.6rem}.tracker{grid-template-columns:1fr 1fr}.top nav{gap:.8rem}.top nav a{font-size:.9rem}}
"""


def date(d, fmt="%b %-d, %Y"):
    return time.strftime(fmt, time.strptime(d, "%Y-%m-%d")) if d else ""


def page(title, desc, body, path=""):
    url = f"{SITE_URL}/{path}"
    brand = f"{MARK}<span>Plain<b>Gov</b></span>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}"><link rel="canonical" href="{url}">
<link rel="icon" href="/favicon.ico" sizes="32x32"><link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png"><link rel="manifest" href="/site.webmanifest"><meta name="theme-color" content="#1d4ed8">
<meta property="og:site_name" content="PlainGov"><meta property="og:type" content="website"><meta property="og:url" content="{url}">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<meta property="og:image" content="{SITE_URL}/og.png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:site" content="@plaingov">
<style>{CSS}</style><script defer src="/_vercel/insights/script.js"></script></head><body>
<a class="skip" href="#main">Skip to content</a>
<header class="top"><div class="wrap"><a class="brand" href="/" aria-label="PlainGov home">{brand}</a>
<nav aria-label="Main"><a href="/us/">Congress</a><a href="/states/">States</a><a href="/about/">About</a></nav></div></header>
<main id="main" class="wrap">{body}</main>
<footer class="site-foot wrap"><div class="foot"><div><a class="brand" href="/">{brand}</a><p>Government, in plain English. Free and nonpartisan.</p></div>
<nav aria-label="Footer"><a href="/us/">Congress</a><a href="/states/">States</a><a href="/about/">About</a><a href="/status/">Data status</a>
<a href="/privacy/">Privacy</a><a href="https://x.com/plaingov">Follow us on X</a><a href="mailto:hello@plaingov.com">Contact</a></nav></div>
<p class="fine">We use AI to turn official bill text into plain English. It can miss things, so check the official link on each page before you rely on it. This isn't legal advice. © {time.strftime('%Y')} PlainGov.</p></footer>
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
    groups = [("New laws", lambda s: s["step"] == 3), ("Vetoed", lambda s: "eto" in s["short"]),
              ("Waiting on a signature", lambda s: s["step"] == 2), ("Halfway there", lambda s: s["step"] == 1),
              ("Proposed", lambda s: s["step"] == 0)]
    out, left = "", recs
    for name, match in groups:
        hit = [r for r in left if match(r["stage"])]
        left = [r for r in left if r not in hit]
        if hit:
            out += f"<h2>{name}</h2>" + items(newest(hit, 25), where)
    return out or "<p>Nothing new here yet. We check every day, so come back soon.</p>"


def bill_page(r):
    st = r["stage"]
    if r["resolution"]:
        where = "House" if r["label"].startswith("H") else "Senate"
        track = f"<p>This is a resolution: basically the {where} saying where it stands. It doesn't become a law.</p>"
    else:
        track = "<ol class=tracker>" + "".join(
            f"<li{' class=done' if i <= st['step'] else ''}{' aria-current=step' if i == st['step'] else ''}>{s}</li>"
            for i, s in enumerate(STEPS)) + "</ol>"
    nxt = f"<p><strong>What's next:</strong> {e(st['next'])}</p>" if st["next"] else ""
    explain = ""
    if r.get("details"):
        explain += "<h2>What it does</h2><ul>" + "".join(f"<li>{e(d)}</li>" for d in r["details"]) + "</ul>"
    if r.get("changes"):
        explain += f"<h2>What changes</h2><p>{e(r['changes'])}</p>"
    if r.get("effective"):
        explain += f"<h2>When it takes effect</h2><p>{e(r['effective'])}</p>"
    if r.get("limited"):
        explain += "<p class=meta>So far we only have this bill's official title and description, so there isn't more to explain yet. The full bill is linked below.</p>"
    record = "".join(f"<li>{e(a)}</li>" for a in reversed(r.get("actions") or []))
    record = f"<p class=meta>From the official record:</p><ul class=meta>{record}</ul>" if record else ""
    sponsor = f"<dt>Who proposed it</dt><dd>{e(r['sponsor'])}</dd>" if r.get("sponsor") else ""
    history = ""
    if len(r["updates"]) > 1:
        history = "<h2>How it got here</h2><ul class=list>" + "".join(
            f"<li>{e(u['now'])}<span class=meta>{date(u['date'])}</span></li>" for u in reversed(r["updates"])) + "</ul>"
    home = f"/states/{r['state'].lower()}/" if r.get("state") else "/us/"
    host = urllib.parse.urlparse(r["official_url"]).netloc.removeprefix("www.")
    credit = "<p class=meta>Bill status from <a href=\"https://legiscan.com\">LegiScan</a> (CC BY 4.0).</p>" if "legiscan.com" in host else ""
    body = f"""<p class=eyebrow><a href="{home}">{e(r['where'])}</a> · {e(r['label'])}</p>
<h1>{e(r['headline'])}</h1>
<p class=lede>{e(r['summary'])}</p>
{explain}
<h2>Where it's at</h2>
<div class=box>{track}<p><strong>What just happened:</strong> {e(r['happened']) if r.get('happened') else f"{e(st['now'])} <span class=meta>{date(r['date'])}</span>"}</p>{nxt}{record}</div>
<h2>The basics</h2>
<dl><dt>Bill number</dt><dd>{e(r['label'])}</dd><dt>Official name</dt><dd>{e(r['title'])}</dd>{sponsor}</dl>
{history}
<a class=official href="{e(r['official_url'])}">Read the full bill on {e(host)} →</a>{credit}"""
    return page(f"{r['headline']} · PlainGov", r["summary"], body, r["id"] + "/")


ABOUT = """<h1>About PlainGov</h1>
<p class=lede>Government moves fast and talks funny. PlainGov keeps up with it for you and explains it in normal words.</p>
<h2>What we do</h2>
<p>Every day we check Congress and all 50 state legislatures for bills that are moving: ones that just passed a vote, got signed into law,
or got vetoed. Then we explain each one in a few plain sentences, with a simple tracker so you can see where it's at.</p>
<h2>Where it comes from</h2>
<p>Straight from the source: <a href="https://www.congress.gov">Congress.gov</a> for Congress, and each state legislature's own website
for the states, using the open-source <a href="https://github.com/openstates/openstates-scrapers">Open States</a> scrapers. For a few states
whose websites block automated visitors, and while a state's website is having trouble, we use <a href="https://legiscan.com">LegiScan</a> (CC BY 4.0). Every page links to the official bill.</p>
<h2>How we use AI</h2>
<p>AI (Anthropic's Claude) rewrites the official text in plain English. Every legislature words things its own way, so AI also reads a bill's official history when it's unclear whether the bill passed, became law, or was vetoed. Another AI model (jev) helps us pick the stories most people would care
about for <a href="https://x.com/plaingov">our X account</a>. AI can get things wrong or leave things out, so if a bill matters to you,
check the official link. This isn't legal advice.</p>
<h2>We don't take sides</h2>
<p>We won't tell you what to think. We just explain what a bill does and where it stands, whoever proposed it.</p>
<h2>Say hi</h2>
<p>Spotted a mistake or have an idea? Email <a href="mailto:hello@plaingov.com">hello@plaingov.com</a> or find us on X at
<a href="https://x.com/plaingov">@plaingov</a>. Our code is open on <a href="https://github.com/redreceipt/plaingov">GitHub</a>, and you can see
how fresh our data is on the <a href="/status/">data status</a> page.</p>"""

PRIVACY = """<h1>Privacy</h1>
<p class=lede>Short version: we don't track you.</p>
<p>No accounts, no ads, no cookies. We count visits with <a href="https://vercel.com/docs/analytics/privacy-policy">Vercel Web Analytics</a>,
which doesn't use cookies or store personal info. It just tells us things like how many people visited and which pages they read.</p>
<p>If you email us, we'll only use your email to write back.</p>
<p>Questions? Email <a href="mailto:hello@plaingov.com">hello@plaingov.com</a>.</p>
<p class=meta>Last updated October 23, 2026.</p>"""

NOT_FOUND = """<h1>Hmm, that page doesn't exist.</h1>
<p class=lede>It may have moved, or the link might be a little off.</p>
<p><a class=more href="/">Go to the home page →</a></p>"""


def build_site():
    site = f"{DATA}/site"
    recs = [json.load(open(p)) for p in glob.glob(f"{DATA}/bills/**/*.json", recursive=True)]
    for r in recs:
        write(f"{site}/{r['id']}/index.html", bill_page(r))
    us = [r for r in recs if not r.get("state")]
    states = [r for r in recs if r.get("state")]
    week = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
    top = sorted((r for r in recs if r["date"] >= week), key=lambda r: r.get("interest") or 0, reverse=True)[:6]
    home = f"""<section class=hero><h1>What's going on in government, in plain English.</h1>
<p class=lede>New bills and laws from Congress and all 50 states, explained like a friend would. No jargon, no spin.</p></section>
{'<h2>Biggest stories this week</h2>' + items(top) if top else ''}
<h2>Latest from Congress</h2>{items(newest([r for r in us if r not in top], 6), False)}<a class=more href="/us/">See everything from Congress →</a>
<h2>Latest from the states</h2>{items(newest([r for r in states if r not in top], 8))}<a class=more href="/states/">Pick your state →</a>"""
    pages = {
        "": ("PlainGov: Government, in plain English", "New bills and laws from Congress and all 50 states, explained like a friend would.", home),
        "us/": ("Congress · PlainGov", "What Congress is up to, in plain English.",
                "<h1>Congress</h1><p class=lede>What the U.S. House and Senate are working on, newest first.</p>" + by_stage(us, False)),
        "about/": ("About · PlainGov", "What PlainGov is, where our info comes from, and how we use AI.", ABOUT),
        "privacy/": ("Privacy · PlainGov", "We don't track you. Here's the short version.", PRIVACY),
        "status/": ("Data status · PlainGov", "How fresh our info is for each state.", status_body(load(HEALTH, {}))),
    }
    counts = {st: sum(r["state"] == st for r in states) for st in STATES}
    grid = "".join(f"<li><a href=\"/states/{st.lower()}/\">{n}<span class=meta>{counts[st] or 'No'} recent update{'' if counts[st] == 1 else 's'}</span></a></li>"
                   for st, n in sorted(STATES.items(), key=lambda kv: kv[1]))
    pages["states/"] = ("Pick your state · PlainGov", "What lawmakers in all 50 states are voting on, in plain English.",
                        f"<h1>Pick your state</h1><p class=lede>See what lawmakers in your state have been voting on.</p><ul class=grid>{grid}</ul>")
    for st, n in STATES.items():
        pages[f"states/{st.lower()}/"] = (f"{n} · PlainGov", f"What {n} lawmakers are voting on, in plain English.",
            f"<p class=eyebrow><a href=\"/states/\">All states</a></p><h1>{n}</h1><p class=lede>What {n} lawmakers have been voting on lately.</p>"
            + by_stage([r for r in states if r["state"] == st], False))
    for path, (title, desc, body) in pages.items():
        write(f"{site}/{path}index.html", page(title, desc, body, path))
    write(f"{site}/404.html", page("Page not found · PlainGov", "That page doesn't exist.", NOT_FOUND))
    shutil.copytree(STATIC, site, dirs_exist_ok=True)
    write(f"{site}/site.webmanifest", json.dumps({"name": "PlainGov", "short_name": "PlainGov", "start_url": "/", "display": "browser",
          "theme_color": "#1d4ed8", "background_color": "#fbfaf7", "icons": [{"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}]}))
    write(f"{site}/robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")
    urls = list(pages) + [r["id"] + "/" for r in recs]
    write(f"{site}/sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
          + "".join(f"<url><loc>{SITE_URL}/{u}</loc></url>" for u in urls) + "</urlset>\n")


def status_body(health):
    label = {"ok": "All good", "running": "Checking now", "failed": "Having trouble", "needs setup": "Coming soon"}
    rows = "".join(
        f"<tr><td><a href=\"/states/{st.lower()}/\">{n}</a></td>"
        f"<td>{'Using backup source' if h.get('status') == 'failed' and h.get('backup') else label.get(h.get('status'), 'Not checked yet')}</td>"
        f"<td>{time.strftime('%b %-d, %-I:%M %p UTC', time.gmtime(h['last_ok'])) if h.get('last_ok') else '—'}</td><td>{h.get('bills', '—')}</td></tr>"
        for st, n, h in sorted(((st, n, health.get(st, {})) for st, n in STATES.items()), key=lambda x: x[1]))
    return ("<h1>Is everything working?</h1><p class=lede>We check every state legislature's website about once a day. "
            "If a site goes down or changes, we get pinged and fix it.</p>"
            f"<table><thead><tr><th>State</th><th>Status</th><th>Last good check</th><th>Bills we're tracking</th></tr></thead><tbody>{rows}</tbody></table>")


def refresh():
    """Rewrite existing pages from before PROMPT_VERSION with fresh sources (bill text, official actions). Queues no posts."""
    stale = lambda id: (load(f"{DATA}/bills/{id}.json", None) or {"v": PROMPT_VERSION}).get("v") != PROMPT_VERSION
    jobs = []
    for st in STATES:
        if st in LEGISCAN_STATES or (st in KEYS and not os.environ.get(KEYS[st])):
            jobs += [(legiscan_update, v) for _, v in legiscan_items(st) if stale(legiscan_id(v))] if os.environ.get("LEGISCAN_API_KEY") else []
        elif scraped(st):
            jobs += [(state_update, v) for _, v in state_items(st, ai_stages(st)) if stale(state_id(v))]
    for f in glob.glob(f"{DATA}/bills/us/*.json"):
        rec = json.load(open(f))
        if rec.get("v") != PROMPT_VERSION:
            c, t, n = re.match(r"us/(\d+)-([a-z]+)-(\d+)$", rec["id"]).groups()
            jobs.append((congress_update, api(f"bill/{c}/{t}/{n}")["bill"]))

    def one(job):
        try:
            return save(job[0](job[1]))
        except Exception as ex:  # left as is; the next refresh tries again
            print(f"FAILED refresh: {ex!r}"[:300], file=sys.stderr, flush=True)

    with ThreadPoolExecutor(6) as pool:
        done = sum(bool(r) for r in pool.map(one, jobs))
    with lock:
        build_site()
    left = sum(json.load(open(f)).get("v") != PROMPT_VERSION for f in glob.glob(f"{DATA}/bills/**/*.json", recursive=True))
    print(f"refresh: {done} of {len(jobs)} pages rewritten; {left} still on an older prompt (no current source, or failed)")


def serve():
    """Static site on :8000 (exe.dev proxy -> Vercel) with our own 404 page and short CDN caching."""
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    site = f"{DATA}/site"

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=site, **k)

        def end_headers(self):
            self.send_header("Cache-Control", "public, max-age=300")
            super().end_headers()

        def send_error(self, code, message=None, explain=None):
            if code != 404 or not os.path.exists(f"{site}/404.html"):
                return super().send_error(code, message, explain)
            body = open(f"{site}/404.html", "rb").read()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()


# --- X ----------------------------------------------------------------------

QUESTION = "Would this affect you?"
EXPERIMENTS_UNTIL = "2026-11-08"  # #12 links and #14 questions alternate until then; compare in the weekly report, then decide


def tweet_text(r, link=True, question=False):
    """One block (what changes, for whom), then bill, status, date, and sponsor. The site keeps its headline + summary.
    Sponsors are named, never @mentioned: X bans automated mentions of people who haven't opted in (docs/x-engagement.md).
    Milestones (passed a chamber or further) link to the bill page unless `link` is off; X charges much more for posts with links."""
    head = f"{r['where']}: "
    ask = f" {QUESTION}" if question else ""
    tail = f"{r['label']} · {r['stage']['short']} ({date(r['date'], '%b %-d' if r['date'][:4] == time.strftime('%Y') else '%b %-d, %Y')})"
    tail += f"\nFrom {r['sponsor']}" if r.get("sponsor") else ""
    url = f"{SITE_URL}/{r['id']}/" if r["milestone"] and link else ""
    room = 280 - len(head) - len(ask) - len(tail) - 2 - (URL_LEN + 1 if url else 0)  # 2 = blank-line separator
    body = r["tweet"]
    if len(body) > room:  # "…" weighs 2 on X, hence room - 2
        body = body[: room - 2].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return f"{head}{body}{ask}\n\n{tail}" + (f"\n{url}" if url else "")


def x_api(method, url, params={}, body=None):
    """X API v2 as @plaingov with OAuth 1.0a user context (query params are signed; JSON bodies are not)."""
    env = lambda k: os.environ[f"X_{k}"]
    q = lambda s: urllib.parse.quote(s, safe="")
    oauth = {"oauth_consumer_key": env("API_KEY"), "oauth_nonce": secrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
             "oauth_token": env("ACCESS_TOKEN"), "oauth_version": "1.0"}
    base = "&".join([method, q(url), q("&".join(f"{q(k)}={q(v)}" for k, v in sorted({**oauth, **params}.items())))])
    key = f"{q(env('API_SECRET'))}&{q(env('ACCESS_SECRET'))}"
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    auth = "OAuth " + ", ".join(f'{q(k)}="{q(v)}"' for k, v in sorted(oauth.items()))
    full = url + ("?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote) if params else "")
    return json.loads(fetch(full, None if body is None else json.dumps(body).encode(), {"Authorization": auth, "Content-Type": "application/json"}))


def post_x(text):
    return x_api("POST", "https://api.x.com/2/tweets", body={"text": text})


METRICS = ["impression_count", "like_count", "reply_count", "retweet_count", "quote_count", "bookmark_count", "user_profile_clicks", "url_link_clicks"]


def report_text(rows):
    """Weekly summary: totals, medians by group, and the top posts."""
    if not rows:
        return "No posts in the last 7 days."
    out = [f"{len(rows)} posts. Totals: " + ", ".join(f"{m.removesuffix('_count')} {sum(r.get(m, 0) for r in rows)}" for m in METRICS), "",
           "Median impressions / likes / replies / profile clicks per post:"]
    for group in ("stage", "where", "link", "question", "hours_et"):
        for value in sorted({str(r[group]) for r in rows}):
            rs = [r for r in rows if str(r[group]) == value]
            med = lambda m: statistics.median(r.get(m, 0) for r in rs)
            out.append(f"  {group} {value} ({len(rs)}): {med('impression_count')} / {med('like_count')} / {med('reply_count')} / {med('user_profile_clicks')}")
    out += ["", "Top posts:"] + [f"  {r.get('impression_count', 0)} impressions, {r.get('like_count', 0)} likes: {r['bill']} https://x.com/plaingov/status/{r['id']}"
                                 for r in sorted(rows, key=lambda r: -r.get("impression_count", 0))[:3]]
    return "\n".join(out)


def report():
    """Weekly X engagement for the last 7 days of posts, emailed with the alerts and kept in state/reports/ to compare
    experiments against. Reading our own posts costs $0.001 each."""
    path = f"{DATA}/state/x.json"
    me = load(path, {}).get("id") or x_api("GET", "https://api.x.com/2/users/me")["data"]["id"]
    write(path, json.dumps({"id": me}))
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7 * 86400))
    tweets = x_api("GET", f"https://api.x.com/2/users/{me}/tweets", {"start_time": since, "max_results": "100",
                   "tweet.fields": "created_at,public_metrics,non_public_metrics"}).get("data", [])
    bills = {p["tweet"]: p["id"] for p in load(f"{DATA}/state/posts.json", [])}
    rows = []
    for t in tweets:
        rec = load(f"{DATA}/bills/{bills.get(t['id'], '-')}.json", None) or {"stage": {}}
        hour = datetime.datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")).astimezone(zoneinfo.ZoneInfo("America/New_York")).hour
        rows.append({"id": t["id"], "bill": bills.get(t["id"], "not ours"), "stage": rec["stage"].get("short", "?"),
                     "where": "state" if rec.get("state") else "federal", "link": "t.co/" in t["text"],
                     "question": QUESTION in t["text"], "hours_et": f"{hour // 6 * 6:02d}-{hour // 6 * 6 + 6:02d}", **t.get("public_metrics", {}), **t.get("non_public_metrics", {})})
    day = time.strftime("%Y-%m-%d")
    write(f"{DATA}/state/reports/{day}.json", json.dumps(rows, indent=1))
    text = report_text(rows)
    if DRY_RUN:
        return print(text)
    alert(f"PlainGov: X report for the week ending {day}", text)
    flush_alerts()


def post():
    with queue_lock():
        post_best()
    reports = sorted(glob.glob(f"{DATA}/state/reports/*.json"))
    if not reports or time.time() - os.path.getmtime(reports[-1]) > 7 * 86400 - 3600:  # weekly, from the hourly post timer
        try:
            report()
        except Exception as ex:  # never let the report break posting
            print(f"X report failed: {ex!r}", file=sys.stderr, flush=True)


POST_HOURS = (8, 21)  # Eastern; a new post's first 2 hours decide its reach, so don't spend them while the U.S. sleeps


def posting_hours(t):
    return POST_HOURS[0] <= datetime.datetime.fromtimestamp(t, zoneinfo.ZoneInfo("America/New_York")).hour < POST_HOURS[1]


def post_best():
    """Post the most interesting update from the last day during POST_HOURS, spacing MAX_POSTS_PER_DAY evenly across them."""
    qpath, lpath = f"{DATA}/state/queue.json", f"{DATA}/state/posts.json"
    now = time.time()
    queue = [q for q in load(qpath, []) if now - q["at"] < 86400]  # day-old news isn't news
    log = load(lpath, [])
    recent = [p for p in log if now - p["at"] < 86400]
    for q in queue:  # scores that failed earlier (e.g. jev's free limit) get another try
        if q["interest"] is None:
            q["interest"] = score(load(f"{DATA}/bills/{q['id']}.json", None) or {})
    best = max((q for q in queue if q["interest"] is not None), key=lambda q: q["interest"], default=None)
    gap = (POST_HOURS[1] - POST_HOURS[0]) * 3600 / MAX_POSTS_PER_DAY
    if not best or best["interest"] < MIN_INTEREST:
        why = "nothing interesting enough"
    elif not posting_hours(now):
        why = f"outside posting hours ({POST_HOURS[0]}:00-{POST_HOURS[1]}:00 Eastern)"
    elif len(recent) >= MAX_POSTS_PER_DAY or (recent and now - recent[-1]["at"] < gap - 300):
        why = "waiting to spread posts out"
    else:
        rec = load(f"{DATA}/bills/{best['id']}.json", None)
        n, testing = len(log), time.strftime("%Y-%m-%d") < EXPERIMENTS_UNTIL  # independent alternations, so each compares cleanly
        text = tweet_text(rec, link=not testing or n % 2 == 0, question=testing and n // 2 % 2 == 0)
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
            rec = save(update(kv[1]))
        except Exception as ex:  # retried next run; one bad item shouldn't block the rest
            return print(f"FAILED {kv[0]}: {ex!r}", file=sys.stderr, flush=True)
        if not first:  # only new updates are post candidates, so only they need a jev score
            score(rec)
        return kv[0], rec

    with ThreadPoolExecutor(6) as pool:
        done = [d for d in pool.map(one, todo) if d]
    # A milestone found late (an AI label, a scraper fix) gets its page, but old news doesn't get posted.
    week = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400))
    fresh = {k for k, v in todo if v.get("_date", "9999") >= week}
    seen |= {k for k, _ in (new if first else done)}  # a first run never retries or posts the backlog
    write(path, json.dumps(sorted(seen)))
    if not first:
        with queue_lock():
            queue = load(qpath, []) + [{"id": r["id"], "key": k, "interest": r.get("interest"), "at": time.time()} for k, r in done if k in fresh]
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
    {"site": build_site, "post": post, "scrape": lambda: scrape(arg), "serve": serve, "refresh": refresh, "report": report}[cmd]()
