import json, os, tempfile, time, urllib.error
import plaingov
from plaingov import ai_stages, take_pages, posting_hours, report_text, doc_text, rewrite, legiscan_items, stage_of, legiscan_update, save_ids, bill_page, bill_stage, by_stage, sponsor_name, state_stage, state_update, status_body, tweet_text, us_stage

rec = {"id": "us/119-s-4618", "where": "U.S. Congress", "state": None, "label": "S. 4618", "resolution": False,
       "title": "School Counseling Act", "official_url": "https://www.congress.gov/bill/119th-congress/senate-bill/4618",
       "sponsor": "Sen. Jane Doe (D-NH)", "date": "2026-05-21", "stage": us_stage("Introduced in Senate"), "milestone": False,
       "headline": "Funds counselors <b>in</b> schools", "tweet": "word " * 100, "summary": "Gives states grants.",
       "updates": [{"date": "2026-05-21", "short": "Proposed", "now": "A senator proposed it."}]}

# Non-milestone posts carry no link; milestone posts link to our page. Both stay within 280 (links count 23, "…" 2).
t = tweet_text(rec)
assert "http" not in t and t.endswith("…\n\nS. 4618 · Proposed (May 21)\nFrom Sen. Jane Doe (D-NH)") and len(t) + 1 <= 280, t
assert tweet_text({**rec, "sponsor": None}).endswith("(May 21)"), "no sponsor line without a sponsor (LegiScan)"
assert t.startswith("U.S. Congress: word word") and "Funds counselors" not in t, t  # one block, no headline
url = "https://plaingov.com/us/119-s-4618/"
t = tweet_text({**rec, "milestone": True})
assert t.endswith("\n" + url) and len(t) - len(url) + 23 + 1 <= 280, t
t = tweet_text({**rec, "milestone": True}, link=False)  # #12 link experiment; no appended discussion prompt
assert "http" not in t and "…\n\nS. 4618" in t and len(t) + 1 <= 280, t
assert "Would this affect you?" not in tweet_text({**rec, "tweet": "Gives states grants."})

# Pages escape model output and link back to their list page.
p = bill_page(rec)
assert "Funds counselors &lt;b&gt;in&lt;/b&gt; schools" in p and "<b>in</b>" not in p
assert "<li class=done aria-current=step>Proposed</li>" in p and 'href="/us/"' in p

assert us_stage("Passed House")["short"] == "Passed the House"
assert us_stage("Senate agreed to House amendment")["step"] == 2
assert us_stage("Public Law")["short"] == "It's law"
# Congress.gov's latest-action wording: one chamber passing says "agreed to" too.
assert us_stage("Passed/agreed to in Senate: Passed Senate with an amendment by Unanimous Consent.")["short"] == "Passed the Senate"
assert us_stage("Resolving differences -- House actions: On motion that the House agree to the Senate amendment Agreed to")["step"] == 2
assert us_stage("Presented to President.")["short"] == "Passed Congress" and us_stage("Vetoed by President.")["short"] == "Vetoed"
assert us_stage("Became Public Law No: 119-45.")["step"] == 3 and us_stage("Read twice and referred to the Committee on Finance.")["short"] == "Proposed"
# No chamber is safer than inferring Senate from the absence of "House".
referral = "Referred to the Committee on Energy and Commerce, and in addition to the Committee on Ways and Means."
assert us_stage(referral)["next"] == "" and "senator" not in us_stage(referral)["now"]
assert us_stage("Read twice and referred to the Committee on Finance.")["next"] == ""
received = us_stage("Received in Senate and referred to the Committee on Finance.")
assert received["next"].startswith("A small group in the Senate") and received["now"] == "The bill is before the Senate."
assert us_stage("Passed/agreed to in Senate: Passed Senate with an amendment to the House bill.")["short"] == "Passed the Senate"
assert us_stage("Passed by unanimous consent.")["short"] == "In Congress"

# Instrument status comes from official evidence, not every HJR/SJR prefix.
ak = {**rec, "id": "states/ak/2025-hjr32", "state": "AK", "where": "Alaska", "label": "HJR 32",
      "source": "Legislative Resolve No. 28", "actions": ["2026-09-30: PERMANENTLY FILED 9/22 LEGIS RESOLVE 28"],
      "stage": us_stage("Public Law"), "updates": []}
r = plaingov.normalize_stage(ak)
assert r["resolution"] and r["stage"]["short"] == "Adopted"
assert "tracker" not in bill_page(r).split('<main id="main"')[1].split("</main>")[0]
assert "<h2>Resolutions</h2>" in by_stage([r], False) and "<h2>New laws</h2>" not in by_stage([r], False)
assert "It's law" not in tweet_text(r)
r = plaingov.normalize_stage({**rec, "label": "H.J.Res. 1", "title": "Making continuing appropriations",
                             "actions": ["2026-05-21: Became Public Law No: 119-1."], "updates": []})
assert not r["resolution"] and r["stage"]["step"] == 3
r = plaingov.normalize_stage({**r, "title": "Proposing an amendment to the Constitution of the United States.",
                             "actions": ["2026-05-21: Passed Senate"], "updates": []})
assert r["amendment"] and not r["resolution"] and "tracker" not in bill_page(r).split('<main id="main"')[1].split("</main>")[0]
assert "<h2>Constitutional amendment proposals</h2>" in by_stage([r], False)
r = plaingov.normalize_stage({**r, "actions": ["2026-05-21: Vetoed by President."]})
assert r["stage"]["short"] == "Vetoed", "never turn a veto into approval"
house = {**rec, "actions": ["2026-05-21: " + referral],
         "stage": {"step": 0, "short": "Proposed", "now": "A senator proposed it.", "next": "Senate"},
         "updates": [{"date": rec["date"], "short": "Proposed", "now": "A senator proposed it."}]}
r = plaingov.normalize_stage(house)
assert r["updates"][-1]["now"] == r["stage"]["now"] and not r["stage"]["next"]
before = json.dumps(r)
assert json.dumps(plaingov.normalize_stage(r)) == before, "repair is idempotent"

assert sponsor_name("Rep. Smith, John [R-TX-5]") == "Rep. John Smith (R-TX)"

# Contact links stay official and do not collect addresses or send messages.
p = bill_page(rec)
assert 'id="participate"' in p and "house.gov/representatives/find-your-representative" in p and "senate.gov/senators/senators-contact.htm" in p
assert "The sponsor may not represent you" in p and "School Counseling Act" in p
p = plaingov.participation({**rec, "state": "NY", "title": "<script>bad</script>"})
assert "nyassembly.gov/mem/search/" in p and "nysenate.gov/find-my-senator" in p
assert "&lt;script&gt;bad&lt;/script&gt;" in p and "<script>" not in p
p = plaingov.participation({**rec, "state": "AK"})
assert "usa.gov/elected-officials" in p and "house.gov/" not in p
assert "<form" not in p and "<input" not in p and "mailto:" not in p

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

SC_BILL = {"state": "SC", "status": (4, None, "2026-09-01"), "identifier": "H 3305", "legislative_session": "2025-2026",
                  "title": "Supplemental Appropriations", "from_organization": LOW, "abstracts": [], "versions": [],
                  "sources": [{"url": "https://www.scstatehouse.gov/billsearch.php?billnumbers=3305"}],
                  "sponsorships": [{"name": "Newton", "primary": True, "entity_type": "person"},
                                   {"name": "Bannister", "primary": True, "entity_type": "person"}]}
s = state_update(SC_BILL)
assert (s["id"], s["label"], s["sponsor"], s["stage"]["short"]) == ("states/sc/2025-2026-h-3305", "H 3305", "Rep. Newton and 1 other", "It's law"), s
assert state_update({**SC_BILL, "classification": ["constitutional amendment"]})["amendment"]
assert state_update({**SC_BILL, "state": "CA"})["sponsor"] == "Asm. Newton and 1 other"  # lower chamber is the Assembly

law = {**rec, "id": "a", "stage": us_stage("Public Law")}
html = by_stage([rec, law], False)
assert html.index("New laws") < html.index("Proposed"), html
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
plaingov.STAGE_PROMPT += " "  # a changed prompt re-checks cached bills
assert ai_stages("VT") == ai and len(asked) == 1, asked
plaingov.STAGE_PROMPT = plaingov.STAGE_PROMPT[:-1]
# An answer without a usable date isn't "no milestone": the classified stage stays and it's asked again next scrape.
plaingov.fetch = lambda url, data, *a: json.dumps({"choices": [{"message": {"content": '{"status": "vetoed", "chamber": ""}'}}]})
try:
    plaingov.judge_stage("VT", json.load(open(f"{plaingov.DATA}/scrape/vt/vt/bill_0.json")))
    raise AssertionError("dateless answer accepted")
except ValueError:
    pass
# Bill text keeps what changed; scripts and styles are dropped.
assert doc_text("data:text/html,<style>p{}</style><p>The fee is <s>$10</s> <u>$20</u>.<u> </u></p>") == "The fee is [removed: $10] [added: $20] .", doc_text("data:text/html,<p>x</p>")

# A host that answers 403 is skipped for a day instead of on every version of every bill.
seen_urls = []
def forbid(url):
    seen_urls.append(url)
    raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
plaingov.doc_text, real_doc_text = forbid, plaingov.doc_text
link = lambda n: {"date": f"2026-0{n}-01", "links": [{"media_type": "text/html", "url": f"https://blocked.example/{n}"}]}
assert plaingov.bill_text({"versions": [link(1), link(2)]}) == "" and seen_urls == ["https://blocked.example/2"], seen_urls
plaingov.doc_text = real_doc_text

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

calls.clear()
rewrite({**rec, "resolution": True, "stage": {"step": 2, "short": "Adopted", "now": "Adopted", "next": ""}, "source": "A resolution urging action."})
assert "This is a resolution, not an enacted law." in calls[0][1] and "This is NOT law yet" not in calls[0][1]

# Pages show the specifics, the official record, and say so when we only have a title.
p = bill_page({**rec, "details": ["Under 21 days old."], "changes": "Adds drop boxes.", "effective": "September 10, 2026",
               "happened": "The Governor signed it on September 9, 2026.", "actions": ["2026-09-09: SIGNED INTO LAW"], "limited": True})
assert all(x in p for x in ("<h2>What it does</h2><ul><li>Under 21 days old.", "What changes", "September 10, 2026", "only have this bill's official title",
                            "The Governor signed it on September 9, 2026.", "<li>2026-09-09: SIGNED INTO LAW</li>")), p
# The weekly X report totals metrics and compares groups (e.g. link vs no link) by median.
rows = [{"id": "1", "bill": "us/a", "stage": "It's law", "where": "federal", "link": True, "question": False, "hours_et": "06-12", "impression_count": 100, "like_count": 3},
        {"id": "2", "bill": "states/x", "stage": "Passed the Senate", "where": "state", "link": False, "question": True, "hours_et": "18-24", "impression_count": 40}]
t = report_text(rows)
assert "2 posts. Totals: impression 140, like 3" in t and "link True (1): 100 / 3 / 0 / 0" in t and t.index("status/1") < t.index("status/2"), t
assert report_text([]) == "No posts in the last 7 days."
# Posts go out during U.S. daytime (8am-9pm Eastern), DST included.
assert posting_hours(1792846800) and not posting_hours(1792803600)  # 9am Oct 24 vs 9pm Oct 23, 2026 (EDT)
assert posting_hours(1799762400) and not posting_hours(1799730000)  # Jan 12, 2027: 9am vs midnight EST
# The daily page cap is shared across feeds and resets each day.
plaingov.MAX_PAGES_PER_DAY = 5
assert (take_pages(3), take_pages(3), take_pages(1)) == (3, 2, 0)
json.dump({"day": "2000-01-01", "pages": 5}, open(f"{plaingov.DATA}/state/budget.json", "w"))
assert take_pages(9) == 5
assert "Using backup source" in status_body({"SC": {"status": "failed", "backup": "LegiScan"}})
# The maintenance command repairs disk records without touching the posting queue.
repair = {**ak, "resolution": False, "stage": us_stage("Public Law"), "v": 5,
          "updates": [{"date": ak["date"], "short": "It's law", "now": "It's officially law now."}]}
path = f"{plaingov.DATA}/bills/{repair['id']}.json"
plaingov.write(path, json.dumps(repair))
plaingov.write(f"{plaingov.DATA}/state/queue.json", '[{"id":"keep-me"}]')
plaingov.HEALTH = f"{plaingov.DATA}/health.json"
plaingov.restage()
fixed = json.load(open(path))
assert fixed["stage"]["short"] == "Adopted" and fixed["v"] == 0
assert fixed["summary"] == repair["summary"] and fixed["updates"][0]["short"] == "Adopted"
before = open(path).read()
plaingov.restage()
assert open(path).read() == before
assert json.load(open(f"{plaingov.DATA}/state/queue.json")) == [{"id": "keep-me"}]
assert not os.path.exists(f"{plaingov.DATA}/state/posts.json")
print("ok")
