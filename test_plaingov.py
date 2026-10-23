from plaingov import bill_page, sc_rows, sc_sponsor, sponsor_name, tweet_text, us_stage

rec = {"id": "us/119-s-4618", "where": "U.S. Congress", "label": "S. 4618", "resolution": False,
       "title": "School Counseling Act", "official_url": "https://www.congress.gov/bill/119th-congress/senate-bill/4618",
       "sponsor": "Sen. Jane Doe (D-NH)", "date": "2026-05-21", "stage": us_stage("Introduced in Senate"),
       "headline": "Funds counselors <b>in</b> schools", "tweet": "word " * 100, "summary": "Gives states grants.",
       "updates": [{"date": "2026-05-21", "short": "Proposed in the Senate", "now": "A senator proposed it."}]}

# Tweets stay within X's 280 limit (links count 23, "…" counts 2) and end with our page link.
t = tweet_text(rec)
url = "https://plaingov.exe.xyz/us/119-s-4618/"
assert t.endswith("…\n\nS. 4618 · Proposed in the Senate (May 21)\n" + url), t
assert len(t) - len(url) + 23 + 1 <= 280, len(t)

# Pages escape model output.
p = bill_page(rec)
assert "Funds counselors &lt;b&gt;in&lt;/b&gt; schools" in p and "<b>in</b>" not in p
assert "<li class=done aria-current=step>Proposed</li>" in p

assert us_stage("Passed House")["short"] == "Passed the House"
assert us_stage("Senate agreed to House amendment")["step"] == 2
assert us_stage("Public Law")["short"] == "Became law"
assert sponsor_name("Rep. Smith, John [R-TX-5]") == "Rep. John Smith (R-TX)"
assert sc_sponsor("Erickson , G.M. Smith and Anderson", "H") == "Rep. Erickson and 2 others"
assert sc_sponsor("Jackson and Davis", "S") == "Sen. Jackson and 1 other"
assert sc_sponsor("House Ways and Means Committee", "H") == "House Ways and Means Committee"

row = ('<tr><td>266</td><td><a href="/billsearch.php?billnumbers=11&amp;session=126&amp;summary=B">S11</a></td><td>249</td>'
       '<td class="summarytext">Paid Family Leave</td><td>06/29/2026</td><td>S</td><td>06/30/2026</td>'
       '<td class="redTextString"></td><td class="redTextString"></td><td>10/01/26</td></tr>'
       '<tr><td>118</td><td><a href="/billsearch.php?billnumbers=5182&session=126&summary=B">H5182</a></td><td></td>'
       '<td>Jasper County Council</td><td>03/25/2026</td><td>V</td><td>03/30/2026</td><td>S</td><td>04/01/2026</td><td></td></tr>'
       '<tr><td>RAT#</td><td>BILL#</td></tr>')
rows = list(sc_rows(row))
assert [(r["stage"]["short"], r["date"]) for _, r in rows] == [("Signed into law", "2026-06-30"), ("Veto stands", "2026-04-01")], rows
assert rows[0][1]["url"] == "https://www.scstatehouse.gov/billsearch.php?billnumbers=11&session=126&summary=B"
print("ok")
