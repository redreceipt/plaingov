from plaingov import legiscan_update, bill_page, bill_stage, by_stage, sponsor_name, state_stage, state_update, status_body, tweet_text, us_stage

rec = {"id": "us/119-s-4618", "where": "U.S. Congress", "state": None, "label": "S. 4618", "resolution": False,
       "title": "School Counseling Act", "official_url": "https://www.congress.gov/bill/119th-congress/senate-bill/4618",
       "sponsor": "Sen. Jane Doe (D-NH)", "date": "2026-05-21", "stage": us_stage("Introduced in Senate"), "milestone": False,
       "headline": "Funds counselors <b>in</b> schools", "tweet": "word " * 100, "summary": "Gives states grants.",
       "updates": [{"date": "2026-05-21", "short": "Just proposed", "now": "A senator just proposed it."}]}

# Non-milestone posts carry no link; milestone posts link to our page. Both stay within 280 (links count 23, "…" 2).
t = tweet_text(rec)
assert "http" not in t and t.endswith("…\n\nS. 4618 · Just proposed (May 21)") and len(t) + 1 <= 280, t
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
print("ok")
