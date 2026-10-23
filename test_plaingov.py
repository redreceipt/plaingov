from plaingov import bill_page, by_stage, sponsor_name, state_stage, state_update, tweet_text, us_stage

rec = {"id": "us/119-s-4618", "where": "U.S. Congress", "state": None, "label": "S. 4618", "resolution": False,
       "title": "School Counseling Act", "official_url": "https://www.congress.gov/bill/119th-congress/senate-bill/4618",
       "sponsor": "Sen. Jane Doe (D-NH)", "date": "2026-05-21", "stage": us_stage("Introduced in Senate"), "milestone": False,
       "headline": "Funds counselors <b>in</b> schools", "tweet": "word " * 100, "summary": "Gives states grants.",
       "updates": [{"date": "2026-05-21", "short": "Proposed in the Senate", "now": "A senator proposed it."}]}

# Non-milestone posts carry no link; milestone posts link to our page. Both stay within 280 (links count 23, "…" 2).
t = tweet_text(rec)
assert "http" not in t and t.endswith("…\n\nS. 4618 · Proposed in the Senate (May 21)") and len(t) + 1 <= 280, t
url = "https://plaingov.com/us/119-s-4618/"
t = tweet_text({**rec, "milestone": True})
assert t.endswith("\n" + url) and len(t) - len(url) + 23 + 1 <= 280, t

# Pages escape model output and link back to their list page.
p = bill_page(rec)
assert "Funds counselors &lt;b&gt;in&lt;/b&gt; schools" in p and "<b>in</b>" not in p
assert "<li class=done aria-current=step>Proposed</li>" in p and 'href="/us/"' in p

assert us_stage("Passed House")["short"] == "Passed the House"
assert us_stage("Senate agreed to House amendment")["step"] == 2
assert us_stage("Public Law")["short"] == "Became law"
assert sponsor_name("Rep. Smith, John [R-TX-5]") == "Rep. John Smith (R-TX)"

assert state_stage("CA", "SB12", 2)["next"] == "The Assembly needs to pass it too."
assert state_stage("TX", "HB5", 2)["short"] == "Passed the House"
assert state_stage("NE", "LB1", 2)["next"] == ""
assert state_stage("SC", "H3305", 5)["short"] == "Vetoed"

s = state_update({"state": "SC", "number": "H3305", "bill_id": 1, "status": "4", "status_date": "2026-09-01",
                  "url": "https://legiscan.com/SC/bill/H3305/2025", "title": "Supplemental Appropriations", "description": "Supplemental Appropriations"})
assert (s["id"], s["label"], s["where"], s["source"]) == ("states/sc/2025-h3305", "H 3305", "South Carolina", "Supplemental Appropriations"), s

law = {**rec, "id": "a", "stage": us_stage("Public Law")}
html = by_stage([rec, law], False)
assert html.index("Became law") < html.index("Just proposed"), html
print("ok")
