#!/usr/bin/env python3
"""PlainGov: bill updates in plain English, as a website and X posts.

Usage: plaingov.py congress|sc   fetch new updates, rewrite them, rebuild the site, post to X
       plaingov.py site          rebuild the site only

A feed's first run builds pages for everything currently listed but posts nothing.
DRY_RUN=1 (default) prints posts instead of sending them.
"""
import base64, glob, hashlib, hmac, html, json, os, re, secrets, sys, time, urllib.parse, urllib.request

DATA = os.environ.get("DATA_DIR", os.path.expanduser("~/plaingov-data"))
SITE_URL = os.environ.get("SITE_URL", "https://plaingov.exe.xyz").rstrip("/")
MAX_POSTS = int(os.environ.get("MAX_POSTS", "5"))
DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-5-5")
UA = "plaingov-bot/1.0 (+https://github.com/redreceipt/plaingov)"
URL_LEN = 23  # X counts every link as 23 chars


def fetch(url, data=None, headers={}):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def strip(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


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
        "id": f"us/{b['congress']}-{t.lower()}-{b['number']}", "where": "U.S. Congress",
        "label": f"{label} {b['number']}", "resolution": t in RESOLUTIONS, "title": b["title"],
        "official_url": f"https://www.congress.gov/bill/{ordinal(b['congress'])}-congress/{slug}/{b['number']}",
        "sponsor": sponsor_name(sponsors[0].get("fullName")), "date": s["actionDate"], "stage": st,
        "source": strip(s["text"]),
    }


# --- South Carolina -------------------------------------------------------

SC = "https://www.scstatehouse.gov"
GOV = {
    "": stage(2, "Passed the Legislature", "The SC House and Senate both passed it.", "The Governor decides whether to sign it."),
    "S": stage(3, "Signed into law", "The Governor signed it. It's now law."),
    "V": stage(2, "Vetoed", "The Governor rejected (vetoed) it.",
               "Lawmakers can still make it law if two-thirds of both the House and Senate vote to override the veto."),
    "NSR": stage(3, "Final", "Lawmakers passed it. It didn't need the Governor's signature."),
    "U": stage(3, "Became law", "It became law without the Governor's signature."),
    "Certain items vetoed": stage(3, "Signed, with cuts", "The Governor signed it but rejected some parts (line-item vetoes).",
                                  "Lawmakers can vote to restore the rejected parts."),
}
VETO = {
    "O": stage(3, "Became law over veto", "Lawmakers overrode the Governor's veto, so it's now law."),
    "S": stage(2, "Veto stands", "Lawmakers didn't override the Governor's veto, so it won't become law."),
}


def iso(us_date):
    m = re.match(r"(\d\d)/(\d\d)/(\d{4})$", us_date)
    return f"{m[3]}-{m[1]}-{m[2]}" if m else ""


def sc_rows(page):
    """Rows of the official Ratifications/Acts log: bills that passed both chambers, plus governor actions."""
    for row in re.findall(r"<tr>(.*?)</tr>", page, re.S):
        link = re.search(r'href="(/billsearch\.php\?[^"]+)"', row)
        cells = [strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if not link or len(cells) != 10:
            continue
        bill, summary, ratified, gov, gov_date, veto, veto_date = cells[1], cells[3], cells[4], cells[5], cells[6], cells[7], cells[8]
        st = dict(VETO.get(veto) or GOV.get(gov) or stage(2, gov, gov))
        url = SC + html.unescape(link.group(1))
        yield f"{url}|{st['short']}", {"bill": bill, "summary": summary, "url": url, "stage": st,
                                       "date": iso(veto_date) or iso(gov_date) or iso(ratified)}


def sc_sponsor(by, chamber):
    """'Erickson , G.M. Smith and Davis' -> 'Rep. Erickson and 2 others'"""
    if "Committee" in by:  # e.g. "House Ways and Means Committee"
        return by
    names = [n.strip() for n in re.split(r",|\band\b", by) if n.strip()]
    first = f"{'Rep.' if chamber == 'H' else 'Sen.'} {names[0]}"
    more = len(names) - 1
    return first + (f" and {more} other{'s' if more > 1 else ''}" if more else "")


def sc_items():
    yield from sc_rows(fetch(SC + "/rats2.php"))


def sc_update(r):
    page = fetch(r["url"])
    title = re.sub(r"\s*-\s*RATIFIED TITLE$", "", strip(re.search(r"<b>Summary:</b>.*?<br>(.*?)<br>", page, re.S).group(1)))
    by = re.search(r"(?:Bill|Resolution), By (.*?)<br>", page, re.S)
    # The title alone is often too thin ("SO AS TO MAKE CERTAIN CHANGES"), so rewrite from the bill text.
    link = re.search(r'href="(/sess[^"]+/bills/\d+\.htm)"', page, re.I)
    text = strip(re.sub(r"<(s|strike|del)\b.*?</\1>", " ", fetch(SC + link.group(1)), flags=re.S | re.I)) if link else ""
    start = text.find(title[:40])
    # shortcut: first 12k chars only; huge bills (budgets) get summarized from their opening sections
    source = text[start:start + 12000] if start >= 0 else title
    num, session = re.search(r"billnumbers=(\d+)&session=(\d+)", r["url"]).groups()
    chamber = r["bill"][0]
    return {
        "id": f"sc/{session}-{chamber.lower()}-{num}", "where": "South Carolina",
        "label": f"{chamber}. {num}", "resolution": False, "title": r["summary"], "official_url": r["url"],
        "sponsor": sc_sponsor(strip(by.group(1)), chamber) if by else None, "date": r["date"], "stage": r["stage"],
        "source": source,
    }


# --- Plain-English rewrite --------------------------------------------------

PROMPT = """You write for PlainGov, which explains government to everyday people.
Rewrite this {where} bill for a general audience, at about an 8th-grade reading level.

Rules:
- Use only facts in the source. Don't guess about effects, costs, or motives.
- Neutral and nonpartisan. No opinions.
- No jargon or legal terms (like "appropriations", "amend", "statute", "section 8-11-155", "local educational agency"). Say it the way you'd explain it to a neighbor.
- Present tense. Start with a verb where natural ("Gives...", "Requires...").
- If the source is brief, keep it brief and describe only what it says. Never mention the source or what it leaves out. Always reply with the JSON.

Reply with only a JSON object with these keys:
"headline": what it does in 4-9 words, sentence case, no period (max 70 characters)
"tweet": one plain sentence about what it does (max 140 characters)
"summary": at most 3 short sentences on what it does and who it affects (max 350 characters)

Official name: {title}
Source: {source}"""


def rewrite(rec):
    body = json.dumps({"model": LLM_MODEL, "max_tokens": 600, "thinking": {"type": "disabled"},
                       "messages": [{"role": "user", "content": PROMPT.format(**rec)}]})
    r = json.loads(fetch("https://llm.int.exe.xyz/v1/messages", body.encode(),
                         {"content-type": "application/json", "anthropic-version": "2023-06-01"}))
    text = "".join(c["text"] for c in r["content"] if c["type"] == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON from LLM (stop_reason={r.get('stop_reason')}): {text[:200]!r}")
    out = {k: " ".join(str(v).split()) for k, v in json.loads(m.group(0)).items()}
    if re.search(r"\bsource\b|\bfull text\b|\b(text|title) (does not|doesn't)\b", out["summary"] + out["tweet"], re.I):
        raise ValueError(f"rewrite talks about its source: {out['summary'][:200]!r}")
    return {k: out[k] for k in ("headline", "tweet", "summary")}


def save(new):
    """Merge an update into the bill's record; only re-run the rewrite when the source text changed."""
    path = f"{DATA}/bills/{new['id']}.json"
    rec = json.load(open(path)) if os.path.exists(path) else {"updates": []}
    if rec.get("source") != new["source"]:
        rec.update(rewrite(new))
    rec.update(new)
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
header,main,footer{max-width:42rem;margin:0 auto;padding:0 1.25rem}
header{padding-top:1.25rem}
.brand{font-weight:800;font-size:1.25rem;color:var(--ink);text-decoration:none}.brand span{color:var(--accent)}
h1{font-size:2rem;line-height:1.2;margin:.25rem 0 1rem}
h2{font-size:1.1rem;margin:2.25rem 0 .75rem}
a{color:var(--accent)}
.eyebrow{color:var(--muted);font-size:.9rem;margin:2rem 0 0}
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
.official{display:inline-block;margin-top:2rem;font-weight:600}
footer{color:var(--muted);font-size:.85rem;margin-top:3rem;padding-bottom:2rem}
footer p{border-top:1px solid var(--line);padding-top:1rem}
@media (max-width:32rem){h1{font-size:1.6rem}.tracker{grid-template-columns:1fr 1fr}}
"""


def date(d):
    return time.strftime("%b %-d, %Y", time.strptime(d, "%Y-%m-%d")) if d else ""


def page(title, desc, body, path=""):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{SITE_URL}/{path}"><meta name="twitter:card" content="summary">
<style>{CSS}</style></head><body>
<header><a class="brand" href="/">Plain<span>Gov</span></a></header>
<main>{body}</main>
<footer><p>Plain-English summaries are written with AI from official records and may leave out details.
Sources: <a href="https://www.congress.gov">Congress.gov</a> and <a href="https://www.scstatehouse.gov">scstatehouse.gov</a>.
<a href="https://github.com/redreceipt/plaingov">Code</a>.</p></footer>
</body></html>
"""


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
    host = urllib.parse.urlparse(r["official_url"]).netloc.removeprefix("www.")
    body = f"""<p class=eyebrow>{e(r['where'])} · {e(r['label'])}</p>
<h1>{e(r['headline'])}</h1>
<p class=lede>{e(r['summary'])}</p>
<h2>Where it stands</h2>
<div class=box>{track}<p><strong>Latest:</strong> {e(st['now'])} <span class=meta>{date(r['date'])}</span></p>{nxt}</div>
<h2>Details</h2>
<dl><dt>Bill number</dt><dd>{e(r['label'])}</dd><dt>Official name</dt><dd>{e(r['title'])}</dd>{sponsor}</dl>
{history}
<a class=official href="{e(r['official_url'])}">Read the official record on {e(host)} →</a>"""
    return page(f"{r['headline']} · PlainGov", r["summary"], body, r["id"] + "/")


def build_site():
    recs = [json.load(open(p)) for p in glob.glob(f"{DATA}/bills/*/*.json")]
    for r in recs:
        write(f"{DATA}/site/{r['id']}/index.html", bill_page(r))
    sections = ""
    for where in ("U.S. Congress", "South Carolina"):
        items = sorted((r for r in recs if r["where"] == where), key=lambda r: r["date"], reverse=True)[:30]
        sections += f"<h2>{where}</h2><ul class=list>" + "".join(
            f"<li><a href=\"/{r['id']}/\">{e(r['headline'])}</a>"
            f"<span class=meta>{e(r['label'])} · {e(r['stage']['short'])} · {date(r['date'])}</span></li>"
            for r in items) + "</ul>"
    body = f"""<h1>What your government is doing, in plain English</h1>
<p class=lede>New bills and updates from Congress and South Carolina, explained without the jargon.</p>{sections}"""
    write(f"{DATA}/site/index.html", page("PlainGov", "What your government is doing, in plain English.", body))


# --- X ----------------------------------------------------------------------

def tweet_text(r):
    url = f"{SITE_URL}/{r['id']}/"
    tail = f"{r['label']} · {r['stage']['short']} ({time.strftime('%b %-d', time.strptime(r['date'], '%Y-%m-%d'))})"
    body = r["tweet"]
    room = 280 - len(r["headline"]) - len(tail) - URL_LEN - 5  # 5 = separator newlines
    if len(body) > room:  # "…" weighs 2 on X, hence room - 2
        body = body[: room - 2].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return f"{r['headline']}\n\n{body}\n\n{tail}\n{url}"


def post_x(text, prefix):
    """POST /2/tweets with OAuth 1.0a user context (JSON bodies are not part of the signature)."""
    env = lambda k: os.environ[f"{prefix}_{k}"]
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


FEEDS = {"congress": (congress_items, congress_update, "X_PLAINGOV"), "sc": (sc_items, sc_update, "X_PLAINGOVSC")}


def run(feed):
    items, update, prefix = FEEDS[feed]
    path = f"{DATA}/state/{feed}.json"
    first = not os.path.exists(path)
    seen = set() if first else set(json.load(open(path)))
    new = [(k, v) for k, v in dict(items()).items() if k not in seen]
    failed = 0
    for k, v in new if first else new[:MAX_POSTS]:
        try:
            rec = save(update(v))
            if not first:
                t = tweet_text(rec)
                print(f"--- {feed} {k}\n{t}" if DRY_RUN else f"posted {k}: {post_x(t, prefix)['data']['id']}", flush=True)
        except Exception as ex:  # retried next run; one bad item shouldn't block the rest
            failed += 1
            print(f"FAILED {k}: {ex!r}", file=sys.stderr, flush=True)
            continue
        seen.add(k)
        write(path, json.dumps(sorted(seen)))
    if first:  # never post the backlog, even items whose page failed to build
        write(path, json.dumps(sorted(k for k, _ in new)))
    build_site()
    print(f"{feed}: {len(new)} new, {failed} failed{' (first run: pages only, no posts)' if first else ''}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    build_site() if sys.argv[1] == "site" else run(sys.argv[1])
