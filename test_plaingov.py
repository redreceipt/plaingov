import json, os, tempfile, time
import plaingov
from plaingov import ai_stages, doc_text, rewrite, legiscan_items, stage_of, legiscan_update, save_ids, bill_page, bill_stage, by_stage, sponsor_name, state_stage, state_update, status_body, tweet_text, us_stage

rec = {"id": "us/119-s-4618", "where": "U.S. Congress", "state": None, "label": "S. 4618", "resolution": False,
       "title": "School Counseling Act", "official_url": "https://www.congress.gov/bill/119th-congress/senate-bill/4618",
       "sponsor": "Sen. Jane Doe (D-NH)", "date": "2026-05-21", "stage": us_stage("Introduced in Senate"), "milestone": False,
       "headline": "Funds counselors <b>in</b> schools", "tweet": "word " * 100, "summary": "Gives states grants.",
       "updates": [{"date": "2026-05-21", "short": "Just proposed", "now": "A senator just proposed it."}]}

# Non-milestone posts carry no link; milestone posts link to our page. Both stay within 280 (links count 23, "…" 2).
t = tweet_text(rec)
assert "http" not in t and t.endswith("…\n\nS. 4618 · Just proposed (May 21)\nFrom Sen. Jane Doe (D-NH)") and len(t) + 1 <= 280, t
assert tweet_text({**rec, "sponsor": None}).endswith("(May 21)"), "no sponsor line without a sponsor (LegiScan)"
assert t.startswith("U.S. Congress: word word") and "Funds counselors" not in t, t  # one block, no headline
url = "https://plaingov.com/us/119-s-4618/"
t = tweet_text({**rec, "milestone": True})
assert t.endswith("\n" + url) and len(t) - len(url) + 23 + 1 <= 280, t

# Pages escape model output and link back to their list page.
p = bill_page(rec)
assert "Funds counselors &lt;b&gt;in&lt;/b&gt; schools" in p and "<b>in</b>" not in p
assert "<li class=done aria-current=step>Proposed</li>" in p and 'href="/us/"' in p

assert us_stage("Passed House")["short"] == "Passed the House"
assert us_stage("Senate agreed to House amendment")["step"] == 2
assert us_stage("Public Law")["short"] == "It's law"
assert sponsor_name("Rep. Smith, John [R-TX-5]") == "Rep. John Smith (R-TX)"

# State milestones come from Open States action classifications.
UP, LOW, GOV = '~{"classification": "upper"}', '~{"classification": "lower"}', '~{"classification": "executive"}'
act = lambda d, kinds, o: {"date": d, "classification": kinds, "organization_id": o, "description": ""}
assert bill_stage({"actions": [act("2026-01-10", ["introduction"], UP)]}) is None
assert bill_stage({"actions": [act("2026-02-01", ["passage"], UP)]}) == (2, "upper", "2026-02-01")
both = [act("2026-02-01", ["passage"], UP), act("2026-03-01", ["passage"], LOW)]
assert bill_stage({"actions": both}) == (3, None, "2026-03-01")
assert bill_stage({"actions": both + [act("2026-03-09", ["executive-veto"], GOV)]}) == (5, None, "2026-03-09")
assert bill_stage({"actions": both + [act("2026-03-05", ["executive-signature"], GOV)]}) == (4, None, "2026-03-05")
assert bill_stage({"actions": [act("2026-02-01T00:00:00", ["passage"], '~{"classification": "legislature"}')]})[0] == 3  # Nebraska
# The AI's reading of a bill's whole history (ai_stages) wins when it differs; agreeing keeps the classified date.
sc_law = {**act("2026-06-04", [], '~{"classification": "legislature"}'), "description": "Act No. 245"}
h1 = {"legislative_session": "2025-2026", "identifier": "H 1", "actions": both + [sc_law]}
assert stage_of(h1, {}) == (3, None, "2026-03-01")
assert stage_of(h1, {"2025-2026|H 1": [4, None, "2026-06-04"]}) == (4, None, "2026-06-04")
assert stage_of(h1, {"2025-2026|H 1": [3, None, "2026-02-27"]}) == (3, None, "2026-03-01")
assert stage_of(h1, {"2025-2026|H 1": [2, "upper", "2026-02-01"]}) == (2, "upper", "2026-02-01")  # e.g. HI second readings
assert stage_of(h1, {"2025-2026|H 1": None}) is None

assert state_stage("CA", 2, "upper")["next"] == "Now the Assembly has to say yes too."
assert state_stage("TX", 2, "lower")["short"] == "Passed the House"
assert state_stage("SC", 5)["short"] == "Vetoed"

s = state_update({"state": "SC", "status": (4, None, "2026-09-01"), "identifier": "H 3305", "legislative_session": "2025-2026",
                  "title": "Supplemental Appropriations", "from_organization": LOW, "abstracts": [], "versions": [],
                  "sources": [{"url": "https://www.scstatehouse.gov/billsearch.php?billnumbers=3305"}],
                  "sponsorships": [{"name": "Newton", "primary": True, "entity_type": "person"},
                                   {"name": "Bannister", "primary": True, "entity_type": "person"}]})
assert (s["id"], s["label"], s["sponsor"], s["stage"]["short"]) == ("states/sc/2025-2026-h-3305", "H 3305", "Rep. Newton and 1 other", "It's law"), s

law = {**rec, "id": "a", "stage": us_stage("Public Law")}
html = by_stage([rec, law], False)
assert html.index("New laws") < html.index("Just proposed"), html
assert "Having trouble" in status_body({"SC": {"status": "failed"}}) and "Coming soon" in status_body({"CA": {"status": "needs setup"}})
ls = legiscan_update({"state": "IL", "status": (2, "upper", "2026-03-01"), "number": "SB1234", "title": "Taxes", "description": "Taxes",
                     "url": "https://legiscan.com/IL/bill/SB1234/2025"})
assert (ls["id"], ls["label"], ls["stage"]["short"], ls["source"]) == ("states/il/2025-sb1234", "SB 1234", "Passed the Senate", "Taxes"), ls
assert "LegiScan" in bill_page({**rec, **ls, "headline": "h", "summary": "s", "updates": []})

# A broken scraper's LegiScan backup reuses the scraper's bill IDs and post keys, and only adds newer milestones.
plaingov.DATA = tempfile.mkdtemp()
os.makedirs(f"{plaingov.DATA}/scrape/sc/sc")
for i, (sess, ident, acts) in enumerate([("2025-2026", "H 3305", [act("2026-02-01", ["passage"], LOW)]), ("2025-2026", "S 12", []),
                                         ("2025-2026", "S 99", []), ("2025e1", "H 3305", [])]):
    json.dump({"legislative_session": sess, "identifier": ident, "classification": ["bill"], "actions": acts}, open(f"{plaingov.DATA}/scrape/sc/sc/bill_{i}.json", "w"))
save_ids("SC")
os.environ["LEGISCAN_API_KEY"] = "test"
ml = lambda year: {"session": {"year_start": year, "special": 0, "session_name": f"{year} Regular"}, **{str(i): {
    "bill_id": i, "number": n, "status": s, "status_date": d, "title": "T", "url": f"https://legiscan.com/SC/bill/{n}/2025"}
    for i, (n, s, d) in enumerate([("H3305", 3, "9999-01-01"), ("H3305", 2, "9999-01-01"), ("S0012", 2, "9999-01-01"), ("H4000", 4, "9999-01-01"),
                                   ("S99", 4, "2020-01-01")])}}  # S99: an old milestone the scraper missed isn't news
plaingov.fetch = lambda *a, **k: json.dumps({"status": "OK", "masterlist": ml(2025)})
got = dict(legiscan_items("SC", json.load(open(f"{plaingov.DATA}/state/ids-sc.json"))))
assert list(got) == ["SC-2025-2026-H 3305-3", "SC-2025-2026-S 12-2"], got
assert legiscan_update(got["SC-2025-2026-H 3305-3"])["id"] == s["id"]
plaingov.fetch = lambda *a, **k: json.dumps({"status": "OK", "masterlist": ml(2027)})
try:
    list(legiscan_items("SC", json.load(open(f"{plaingov.DATA}/state/ids-sc.json"))))
    raise AssertionError("matched bills across sessions")
except RuntimeError:
    pass
# ai_stages: the stage model reads recently active bills short of law (cached); old and law bills are skipped.
today = time.strftime("%Y-%m-%d")
recent = [act(today, ["passage"], UP), act(today, ["passage"], LOW), {**sc_law, "date": today}]
os.makedirs(f"{plaingov.DATA}/scrape/vt/vt")
for i, (ident, acts) in enumerate([("H 1", recent), ("H 2", both + [sc_law]), ("H 3", recent + [act(today, ["executive-signature"], GOV)])]):
    json.dump({"legislative_session": "2025-2026", "identifier": ident, "title": "T", "classification": ["bill"], "actions": acts},
              open(f"{plaingov.DATA}/scrape/vt/vt/bill_{i}.json", "w"))
asked = []
def fake(url, data, *a):
    req = json.loads(data)
    asked.append((req["model"], req["messages"][0]["content"]))
    return json.dumps({"choices": [{"message": {"content": f'{{"status": "law", "chamber": "", "date": "{today}"}}'}, "finish_reason": "stop"}]})
plaingov.fetch = fake
ai = ai_stages("VT")
assert ai == {"2025-2026|H 1": [4, None, today]}, ai  # H 2 is old news, H 3 is already law
assert [m for m, _ in asked] == ["gpt-6-luna"] and "bill H 1 " in asked[0][1] and "Act No. 245" in asked[0][1], asked
asked.clear()
assert ai_stages("VT") == ai and not asked  # cached
# An answer without a usable date isn't "no milestone": the classified stage stays and it's asked again next scrape.
plaingov.fetch = lambda url, data, *a: json.dumps({"choices": [{"message": {"content": '{"status": "vetoed", "chamber": ""}'}}]})
try:
    plaingov.judge_stage("VT", json.load(open(f"{plaingov.DATA}/scrape/vt/vt/bill_0.json")))
    raise AssertionError("dateless answer accepted")
except ValueError:
    pass
# Bill text keeps what changed; scripts and styles are dropped.
assert doc_text("data:text/html,<style>p{}</style><p>The fee is <s>$10</s> <u>$20</u>.<u> </u></p>") == "The fee is [removed: $10] [added: $20] .", doc_text("data:text/html,<p>x</p>")

# rewrite: Claude writes, the stage model checks against the source, and Claude retries once with the problems.
calls = []
def fake(url, data, *a):
    req = json.loads(data)
    calls.append((req["model"], req["messages"][0]["content"]))
    if "claude" in req["model"]:  # first a vague answer, then a concrete one
        out = {"headline": "Rules for certain places", "tweet": "t", "summary": "s", "details": "one", "changes": "", "effective": "", "happened": "h", "limited": False}
        if len(calls) > 2:
            out = {**out, "headline": "Hospitals and fire stations can take newborns", "details": ["Under 21 days old."]}
        return json.dumps({"content": [{"type": "text", "text": json.dumps(out)}]})
    verdict = {"vague": True, "unsupported": []} if len(calls) == 2 else {"vague": False, "unsupported": []}
    return json.dumps({"choices": [{"message": {"content": json.dumps(verdict)}}]})
plaingov.fetch = fake
r = rewrite({**rec, "source": "Hospitals and fire stations may accept infants under 21 days old.", "actions": ["2026-09-09: Signed"]})
assert r["headline"] == "Hospitals and fire stations can take newborns" and r["details"] == ["Under 21 days old."], r
assert [m for m, _ in calls] == ["claude-haiku-5-5", "gpt-6-luna"] * 2 and "Too vague" in calls[2][1] and "2026-09-09: Signed" in calls[0][1], calls

# Pages show the specifics, the official record, and say so when we only have a title.
p = bill_page({**rec, "details": ["Under 21 days old."], "changes": "Adds drop boxes.", "effective": "September 10, 2026",
               "happened": "The Governor signed it on September 9, 2026.", "actions": ["2026-09-09: SIGNED INTO LAW"], "limited": True})
assert all(x in p for x in ("<h2>What it does</h2><ul><li>Under 21 days old.", "What changes", "September 10, 2026", "only have this bill's official title",
                            "The Governor signed it on September 9, 2026.", "<li>2026-09-09: SIGNED INTO LAW</li>")), p
assert "Using backup source" in status_body({"SC": {"status": "failed", "backup": "LegiScan"}})
print("ok")
