"""Identity joins and ingestion boundaries; live-source proof is kept outside Git."""
import copy, csv, email.utils, io, json, os, ssl, tempfile, time, unittest
from unittest.mock import Mock, patch
import plaingov as p
import state_rosters as rosters

PERSON = "ocd-person/11111111-1111-4111-8111-111111111111"
OTHER = "ocd-person/22222222-2222-4222-8222-222222222222"
URL = "https://legislature.example.gov/member/7"
YEAR = int(time.strftime("%Y", time.gmtime()))
SESSION = {"session_id": 10, "state_id": 1, "state_abbr": "AL", "year_start": YEAR,
           "year_end": YEAR, "session_name": "100th Legislature", "session_title": "Regular Session", "special": 0, "prefile": 0}
PERSON_ROW = {"id": PERSON, "name": "Jane Doe", "current_chamber": "lower", "links": URL,
              "sources": "https://justfacts.votesmart.org/candidate/biography/123/jane-doe;https://ballotpedia.org/Jane_Doe"}
LEGISLATOR = {"people_id": 7, "state_id": 1, "name": "Jane Doe", "first_name": "Jane", "last_name": "Doe",
              "votesmart_id": 123, "ballotpedia": "Jane_Doe", "role": "Rep", "committee_sponsor": 0,
              "bio": {"social": {"biography": URL}}}


class SponsorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patches = [patch.object(p, "DATA", self.temp.name), patch.dict(os.environ, {"LEGISCAN_API_KEY": "test-only"})]
        for change in self.patches:
            change.start()
            self.addCleanup(change.stop)

    def profiles(self, st="AL", people=None, legislators=None, official=None, sessions=None, response_session=None, wanted=None):
        session = {**SESSION, "state_abbr": st}
        people = [copy.deepcopy(PERSON_ROW)] if people is None else people
        legislators = [copy.deepcopy(LEGISLATOR)] if legislators is None else legislators
        if st == "NE":
            people[0]["current_chamber"] = "legislature"
            legislators[0]["role"] = "Sen"
        source = [(rosters.member_key(st, URL), URL, "Representative Jane Doe")] if official is None else official
        api = lambda op, **kw: ({"sessions": [session] if sessions is None else sessions} if op == "getSessionList" else
                               {"sessionpeople": {"session": session if response_session is None else response_session, "people": legislators}})
        with patch.object(p, "state_people", return_value=people), patch.object(p, "sponsor_api", side_effect=api), patch.object(rosters, "members", return_value=source):
            return p.state_profiles(st, {f"{st}:ls:7"} if wanted is None else wanted)

    def test_join_is_state_scoped_in_every_generic_jurisdiction(self):
        for st in p.STATES:
            if st == "MA":
                continue  # MA retains its official Presenter/current-directory gate.
            with self.subTest(state=st):
                result = self.profiles(st)
                self.assertEqual(result[f"{st}:ls:7"][0], URL)
                self.assertEqual(result[f"{st}:{PERSON}"], result[f"{st}:ls:7"])
                self.assertTrue(all(key.startswith(st + ":") for key in result))

    def test_no_names_only_or_ambiguous_external_identifiers(self):
        self.assertEqual(self.profiles(legislators=[{**LEGISLATOR, "votesmart_id": 0, "ballotpedia": ""}]), {})
        self.assertEqual(self.profiles(people=[PERSON_ROW, {**PERSON_ROW, "id": OTHER}]), {})
        conflicting = {**PERSON_ROW, "id": OTHER, "sources": "https://ballotpedia.org/Jane_Doe"}
        first = {**PERSON_ROW, "sources": "https://votesmart.org/candidate/123/jane-doe"}
        self.assertEqual(self.profiles(people=[first, conflicting]), {})
        self.assertEqual(self.profiles(legislators=[LEGISLATOR, {**LEGISLATOR, "people_id": 8}], wanted={"AL:ls:7"}), {})
        self.assertEqual(self.profiles(legislators=[LEGISLATOR, LEGISLATOR]), {})

    def test_current_primary_source_not_aggregators_or_reused_seat(self):
        self.assertEqual(self.profiles(official=[]), {})
        self.assertEqual(self.profiles(official=[(rosters.member_key("AL", URL), URL, "Janet Roe")]), {})
        reused = [(rosters.member_key("AL", URL), URL, name) for name in ("Jane Doe", "Janet Roe")]
        self.assertEqual(self.profiles(official=reused), {})
        self.assertEqual(self.profiles(people=[]), {})
        self.assertEqual(self.profiles(legislators=[{**LEGISLATOR, "role": "Sen"}]), {})
        self.assertEqual(self.profiles(legislators=[{**LEGISLATOR, "state_id": 2}]), {})
        self.assertEqual(self.profiles(response_session={**SESSION, "session_id": 11}), {})
        self.assertEqual(self.profiles(response_session={**SESSION, "state_id": 2}), {})
        self.assertEqual(self.profiles(sessions=[{**SESSION, "year_start": YEAR + 1}]), {})
        self.assertEqual(self.profiles(sessions=[{**SESSION, "prefile": 1}]), {})
        self.assertEqual(self.profiles(sessions=[{**SESSION, "session_title": "1st Special Session"}]), {})
        self.assertEqual(self.profiles(sessions=[{**SESSION, "state_abbr": "CA"}]), {})
        for url in ("javascript:alert(1)", "https://user:password@legislature.example.gov/member/7"):
            self.assertEqual(self.profiles(official=[(rosters.member_key("AL", URL), url, "Jane Doe")]), {})
        other_url = "https://legislature.example.gov/member/8"
        person = {**PERSON_ROW, "links": URL + ";" + other_url}
        source = [(rosters.member_key("AL", u), u, "Jane Doe") for u in (URL, other_url)]
        self.assertEqual(self.profiles(people=[person], official=source), {})

    def test_same_name_url_owners_are_checked_before_requested_ids(self):
        other = {**PERSON_ROW, "id": OTHER, "sources": "https://votesmart.org/candidate/999/other"}
        second = {**LEGISLATOR, "people_id": 8, "votesmart_id": 999, "ballotpedia": ""}
        self.assertEqual(self.profiles(people=[PERSON_ROW, other], legislators=[LEGISLATOR, second]), {})
        self.assertEqual(self.profiles(people=[PERSON_ROW, other]), {}, "an unrequested current identity still makes the shared name/URL ambiguous")
        self.assertIn("AL:ls:7", self.profiles(people=[PERSON_ROW, {**other, "name": "Janet Roe"}]))
        destination = "https://legislature.example.gov/member/8"
        other = {**other, "links": destination}
        second = {**second, "bio": {"social": {"biography": destination}}}
        aliases = [(rosters.member_key("AL", u), destination, "Jane Doe") for u in (URL, destination)]
        self.assertEqual(self.profiles(people=[PERSON_ROW, other], legislators=[LEGISLATOR, second], official=aliases), {},
                         "two owners of old/current aliases are still ambiguous")
        self.assertEqual(self.profiles(people=[PERSON_ROW, other], legislators=[LEGISLATOR, second], official=aliases, wanted={"AL:ls:8"}), {})
        self.assertEqual(self.profiles(people=[PERSON_ROW, other], legislators=[LEGISLATOR, second], official=aliases[:1]), {},
                         "an unrequested/unavailable legacy alias must not hide a same-name claimant")

    def test_hawaii_directory_does_not_resolve_disagreeing_profile_ids(self):
        canonical = "https://www.capitol.hawaii.gov/legislature/memberpage.aspx?member=201"
        search = canonical.replace("201", "114")
        person = {**PERSON_ROW, "links": canonical, "current_chamber": "upper"}
        legislator = {**LEGISLATOR, "role": "Sen", "bio": {"social": {"biography": canonical}}}
        rows = [(rosters.member_key("HI", search), "https://search.capitol.hawaii.gov/?type=legislator-addresses", "Jane Doe")]
        self.assertEqual(self.profiles("HI", people=[person], legislators=[legislator], official=rows), {})
        legislator["bio"]["social"]["biography"] = search
        self.assertEqual(self.profiles("HI", people=[person], legislators=[legislator], official=rows), {},
                         "a stale alternate ID cannot override a disagreeing supplied canonical ID")
        person["links"] = search
        self.assertTrue(self.profiles("HI", people=[person], legislators=[legislator], official=rows))

    def test_membership_without_destination_does_not_link_candidate(self):
        self.assertEqual(self.profiles(official=[(rosters.member_key("AL", URL), None, "Jane Doe")]), {})

    def test_enrichment_prioritizes_unfinished_records_with_a_renewed_budget(self):
        for number in (1, 2):
            bill = {"legislative_session": "2026", "identifier": f"HB {number}", "sponsorships": []}
            p.write(f"{p.DATA}/scrape/al/al/bill_{number}.json", json.dumps(bill))
            record = {"id": f"states/al/2026-hb-{number}"}
            if number == 1:
                record.update(sponsor_legiscan_id=7, sponsor_identity_source=99)
            p.write(f"{p.DATA}/bills/{record['id']}.json", json.dumps(record))
        calls = []
        def lookup(bill):
            calls.append(bill["identifier"])
            return {"sponsor_legiscan_id": 8, "sponsor_identity_source": 100} if len(calls) == 1 else {}
        with patch.object(p, "scraped_sponsor", side_effect=lookup):
            p.enrich_sponsors("AL")
        self.assertEqual(calls[0], "HB 2", "the first renewed lookup must not repeat a completed bill")
        self.assertEqual(p.load(f"{p.DATA}/bills/states/al/2026-hb-2.json", {})["sponsor_legiscan_id"], 8)

    def test_metadata_uses_an_explicit_primary_person(self):
        person = {**LEGISLATOR, "sponsor_type_id": 1, "sponsor_order": 1}
        bill = {"bill_id": 99, "state": "AL", "sponsors": [person]}
        self.assertEqual(p.sponsor_metadata("AL", bill), {"sponsor_legiscan_id": 7, "sponsor_identity_source": 99, "sponsor": "Jane Doe"})
        for sponsors in (None, [None], [person, person], [person, {**person, "people_id": 8}],
                         [person, {**person, "committee_sponsor": 1}], [{**person, "sponsor_order": 2}],
                         [{**person, "people_id": True}], [{**person, "name": None}], [{**person, "sponsor_order": True}]):
            self.assertEqual(p.sponsor_metadata("AL", {**bill, "sponsors": sponsors}), {})
        for sponsors in ([], [{**person, "committee_sponsor": 1}], [{**person, "sponsor_type_id": 2}],
                         [{**person, "committee_sponsor": 1}, {**person, "sponsor_order": 2}]):
            self.assertEqual(p.sponsor_metadata("AL", {**bill, "sponsors": sponsors}),
                             {"sponsor_legiscan_id": None, "sponsor_identity_source": None, "sponsor": None})
        self.assertEqual(p.sponsor_metadata("CA", bill), {})
        self.assertEqual(p.sponsor_metadata("AL", {**bill, "bill_id": True}), {})

    def test_session_aliases_do_not_cross_special_sessions_or_states(self):
        regular = {**SESSION, "session_id": 1}
        special = {**SESSION, "session_id": 2, "special": 1, "session_title": f"{YEAR} 1st Special Session"}
        self.assertEqual(p.sponsor_session("AL", f"{YEAR}rs", [regular, special]), regular)
        self.assertEqual(p.sponsor_session("AL", f"{YEAR}S1", [regular, special]), special)
        self.assertIsNone(p.sponsor_session("AL", str(YEAR), [special]))
        self.assertIsNone(p.sponsor_session("CA", str(YEAR), [regular]))
        self.assertIsNone(p.sponsor_session("AL", str(YEAR), [regular, {**regular, "session_id": 3}]))
        second = {**special, "session_id": 4, "session_title": f"{YEAR} 2nd Special Session"}
        self.assertIsNone(p.sponsor_session("AL", f"{YEAR}_ss", [special, second]))
        self.assertIsNone(p.sponsor_session("AL", f"{YEAR}S3", [regular, special, second]))
        self.assertIsNone(p.sponsor_session("AZ", "57th-2nd-regular", [{**regular, "session_id": 2235}]))

    def test_authoritative_sponsor_corrections_do_not_inherit_old_ids(self):
        person = {**LEGISLATOR, "sponsor_type_id": 1, "sponsor_order": 1}
        item = {"state": "AL", "status": (4, "lower", "2026-01-01"), "number": "HB10", "bill_id": 99,
                "title": "A bill", "url": "https://legiscan.com/AL/bill/HB10/2026"}
        def update(sponsors):
            bill = {"bill_id": 99, "state": "AL", "bill_number": "HB10", "sponsors": sponsors}
            with patch.object(p, "fetch", return_value=json.dumps({"bill": bill})):
                return p.legiscan_update(item)
        initial = update([person])
        original = {**initial, "sponsor_person_id": PERSON, "sponsor_profile_id": "old-profile", "v": p.PROMPT_VERSION,
                    "headline": "A bill", "summary": "Keep this explanation", "publication_copy": "Keep this exact copy",
                    "updates": [{"date": initial["date"], "short": initial["stage"]["short"], "now": initial["stage"]["now"]}]}
        path = f"{p.DATA}/bills/{initial['id']}.json"
        with patch.object(p, "rewrite", side_effect=AssertionError("metadata must not rewrite prose")):
            for sponsors in ([], [{**person, "committee_sponsor": 1}], [{**person, "people_id": 8, "name": "Janet Roe"}]):
                p.write(path, json.dumps(original))
                result = p.save(update(sponsors))
                self.assertIsNone(result["sponsor_person_id"])
                self.assertIsNone(result["sponsor_profile_id"])
                for key in ("summary", "publication_copy", "updates"):
                    self.assertEqual(result[key], original[key])
            ocd_only = {k: v for k, v in original.items() if k not in ('sponsor_legiscan_id', 'sponsor_identity_source')}
            p.write(path, json.dumps(ocd_only))
            result = p.save(update([{**person, "people_id": 8, "name": "Janet Roe"}]))
            self.assertIsNone(result["sponsor_person_id"], "cross-namespace replacement cannot inherit the previous OCD identity")
            p.write(path, json.dumps(original))
            result = p.save(update([person, {**person, "people_id": 8}]))
            self.assertEqual(result["sponsor_legiscan_id"], 7, "ambiguous primary ordering is not confirmed removal")
            result = p.save(update(None))
            self.assertEqual(result["sponsor_legiscan_id"], 7, "malformed/unavailable evidence preserves the established identity")
            self.assertEqual(result["sponsor_person_id"], PERSON)
            changed = {**update(None), "sponsor": "Janet Roe"}
            result = p.save(changed)
            profile = self.profiles()["AL:ls:7"]
            evidence = {"links": {"AL:ls:7": profile, "AL:" + PERSON: profile}, "checked": "2026-01-01"}
            self.assertNotIn('href="' + URL + '"', p.bill_page(result, evidence), "old IDs cannot link a conflicting new display name")

    def test_enrichment_changes_only_ids_and_leaves_queue_history_and_prose_alone(self):
        bill = {"legislative_session": "2026", "identifier": "HB 10", "sponsorships": []}
        p.write(f"{p.DATA}/scrape/al/al/bill_1.json", json.dumps(bill))
        record = {"id": "states/al/2026-hb-10", "sponsor": "Jane Doe", "summary": "Keep the prose",
                  "v": 1, "updates": [{"date": "2026-01-01"}], "sponsor_person_id": OTHER}
        target = f"{p.DATA}/bills/{record['id']}.json"
        p.write(target, json.dumps(record))
        for name in ("queue", "posts", "states-al"):
            p.write(f"{p.DATA}/state/{name}.json", '["unchanged"]')
        metadata = {"sponsor_legiscan_id": 7, "sponsor_identity_source": 99}
        with patch.object(p, "scraped_sponsor", return_value=metadata), patch.object(p, "rewrite", side_effect=AssertionError("no AI")):
            p.enrich_sponsors("AL")
            result = p.load(target, {})
            self.assertEqual(result, {**record, "sponsor_person_id": None, "sponsor_profile_id": None, **metadata})
            for name in ("queue", "posts", "states-al"):
                self.assertEqual(p.load(f"{p.DATA}/state/{name}.json", []), ["unchanged"])
            with patch.object(p, "write", side_effect=AssertionError("idempotent enrichment")):
                p.enrich_sponsors("AL")
        with patch.object(p, "scraped_sponsor", return_value={}):
            p.enrich_sponsors("AL")
            self.assertEqual(p.load(target, {}), result)
        for invalid in (None, "ZZ", "MA"):
            with self.assertRaises(ValueError):
                p.enrich_sponsors(invalid)

    def test_dataset_access_key_is_not_logged_or_used_in_cleartext_cache_key(self):
        with patch.object(p, "fetch", side_effect=OSError("unavailable")), patch("sys.stderr", new=io.StringIO()) as errors:
            self.assertEqual(p.sponsor_api("getDataset", id=10, access_key="test-private-access-key"), {})
        self.assertNotIn("test-private-access-key", errors.getvalue())
        self.assertIn("getDataset", errors.getvalue())

    def test_nd_assembly_alone_cannot_select_regular_over_special(self):
        regular = {**SESSION, "state_abbr": "ND", "session_name": "69th Legislative Assembly"}
        special = {**regular, "session_id": 11, "special": 1, "session_title": "2026 2nd  Special Session"}
        base = "https://ndlegis.gov/assembly/69-2025/"
        self.assertIsNone(p.sponsor_session("ND", "69", [regular, special]))
        self.assertEqual(p.sponsor_session("ND", "69", [regular, special], [base + "special-2/bill-overview/bo2407.html"]), special)
        self.assertEqual(p.sponsor_session("ND", "69", [regular, special], [base + "regular/bill-overview/bo2407.html"]), regular)
        self.assertIsNone(p.sponsor_session("ND", "68", [regular, special], [base + "regular/bill-overview/bo2407.html"]))
        self.assertIsNone(p.sponsor_session("ND", "69", [regular], [base + "special-2/bill-overview/bo2407.html"]))
        for bad in (base.replace('https:', 'javascript:'), base.replace('ndlegis.gov', 'ndlegis.gov.evil.test'), base.replace('ndlegis.gov', 'user@ndlegis.gov')):
            self.assertIsNone(p.sponsor_session("ND", "69", [regular], [bad + "regular/bill-overview/bo2407.html"]))
        self.assertIsNone(p.sponsor_session("ND", "69", [regular, special], [base + "regular/bill-overview/bo2407.html", base + "special-2/bill-overview/bo2407.html"]))

    def test_ri_number_alias_does_not_change_other_states_or_instruments(self):
        self.assertEqual(p.bill_num("HB 0001", "RI"), "H1")
        self.assertEqual(p.bill_num("SB3314", "RI"), "S3314")
        self.assertEqual(p.bill_num("HJR 0001", "RI"), "HJR1")
        self.assertEqual(p.bill_num("HB 0001", "CA"), "HB1")
        self.assertEqual(p.bill_num("A00001", "NY"), "A1")

    def test_native_bill_requires_session_number_and_state_agreement(self):
        raw = {"state": "AL", "legislative_session": str(YEAR), "identifier": "HB 10", "sponsorships": [{"primary": True, "entity_type": "person"}]}
        bill = {"state": "AL", "bill_id": 99, "bill_number": "HB10", "session": SESSION,
                "sponsors": [{**LEGISLATOR, "sponsor_type_id": 1, "sponsor_order": 1}]}
        def api(op, **kwargs):
            return {"getSessionList": {"sessions": [SESSION]}, "getMasterList": {"masterlist": {"1": {"bill_id": 99, "number": "HB10"}}}, "getBill": {"bill": bill}}[op]
        with patch.object(p, "sponsor_api", side_effect=api):
            self.assertEqual(p.scraped_sponsor(raw)["sponsor_legiscan_id"], 7)
            for field, bad in (("bill_number", "HB11"), ("state", "CA"), ("session", {**SESSION, "session_id": 11})):
                original = bill[field]
                bill[field] = bad
                self.assertIsNone(p.scraped_sponsor(raw)["sponsor_legiscan_id"])
                bill[field] = original
            self.assertIsNone(p.scraped_sponsor({**raw, "state": "MA"})["sponsor_legiscan_id"])
        with patch.object(p, "sponsor_api", return_value={}):
            self.assertEqual(p.scraped_sponsor(raw), {}, "outages must not erase a previously verified identity")

    def test_rendering_keeps_conflicts_plain_and_directory_identity_explicit(self):
        record = {"id": "states/al/test-hb1", "state": "AL", "where": "Alabama", "label": "HB 1", "title": "Test bill",
                  "headline": "Test bill", "summary": "A test.", "date": "2026-01-01", "resolution": False,
                  "official_url": "https://legislature.example.gov/bill/1", "sponsor": "Jane <Doe>", "sponsor_legiscan_id": 7,
                  "updates": [{}], "stage": p.state_stage("AL", 3)}
        profile = (URL, "Official legislative directory: Jane <Doe>", "Current Alabama legislative roster", "AL:" + PERSON, ("jane doe", "doe"))
        evidence = {"links": {"AL:ls:7": profile, "AL:" + PERSON: profile}, "checked": "2026-01-01"}
        page = p.bill_page(record, evidence)
        self.assertIn('href="' + URL + '"', page)
        self.assertIn("Jane &lt;Doe&gt;", page)
        self.assertIn('title="Official legislative directory: Jane &lt;Doe&gt;"', page)
        self.assertIn("Find your state representatives", page)
        self.assertIn("The sponsor may not represent you", page)
        self.assertIn('href="' + URL + '"', p.bill_page({**record, "sponsor": "Rep. Doe and 2 others"}, evidence))
        assembly = p.bill_page({**record, "sponsor": "Asm. Jane Doe"}, evidence)
        self.assertIn('href="' + URL + '"', assembly)
        self.assertIn("Asm. Jane Doe", assembly)
        for displayed in ("Janet Roe", "Jane Q. Doe"):
            self.assertNotIn('href="' + URL + '"', p.bill_page({**record, "sponsor": displayed}, evidence))
        record["sponsor_person_id"] = OTHER
        self.assertNotIn('href="' + URL + '"', p.bill_page(record, evidence))
        # Same-named people in the same shared directory still have different identities.
        evidence["links"]["AL:" + OTHER] = (*profile[:3], "AL:" + OTHER, profile[4])
        self.assertNotIn('href="' + URL + '"', p.bill_page(record, evidence))
        record["sponsor_person_id"] = PERSON
        self.assertIn('href="' + URL + '"', p.bill_page(record, evidence))

    def test_one_state_failure_does_not_remove_another_states_evidence(self):
        profile = (URL, "Official information", "Current roster", "CA:" + PERSON)
        def resolve(st, identities):
            if st == "AL":
                raise OSError("unavailable")
            self.assertIn("CA:ls:7", identities)
            return {"CA:ls:7": profile}
        with patch.object(p, "state_profiles", side_effect=resolve):
            result = p.sponsor_profiles([{"state": st, "sponsor_legiscan_id": 7} for st in ("AL", "CA")])
        self.assertEqual(result["links"], {"CA:ls:7": profile})

    def test_cache_expires_without_reusing_failed_or_future_membership(self):
        read = Mock(return_value=["current"])
        now = time.time()
        with patch.object(p.time, "time", return_value=now):
            self.assertEqual(p.sponsor_cache("roster:test", read), ["current"])
            self.assertEqual(p.sponsor_cache("roster:test", read), ["current"])
        self.assertEqual(read.call_count, 1)
        read.side_effect = OSError("source unavailable")
        with patch.object(p.time, "time", return_value=now + 86400):
            self.assertIsNone(p.sponsor_cache("roster:test", read))
            self.assertIsNone(p.sponsor_cache("roster:test", read))
        self.assertEqual(read.call_count, 2)
        read.side_effect = None
        read.return_value = ["recovered"]
        with patch.object(p.time, "time", return_value=now + 90000):
            self.assertEqual(p.sponsor_cache("roster:test", read), ["recovered"])
        with patch.object(p.time, "time", return_value=now):
            p.sponsor_cache("roster:test", read)
        self.assertEqual(read.call_count, 4, "future-dated cache must not count as a fresh check")

    def test_roster_tls_completes_chains_without_new_root_trust(self):
        original_roots = {r['subject'] for r in ssl.create_default_context().get_ca_certs() if r['subject'] == r['issuer']}
        with patch.object(p, "fetch", return_value="verified") as request:
            self.assertEqual(p.roster_fetch("https://www.cga.ct.gov/asp/menu/hlist.asp"), "verified")
            context = request.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.verify_flags & ssl.VERIFY_X509_STRICT)
        self.assertFalse(context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN)
        self.assertEqual({r['subject'] for r in context.get_ca_certs() if r['subject'] == r['issuer']}, original_roots)
        with patch.object(p, "fetch") as request:
            with self.assertRaises(ValueError):
                p.roster_fetch("http://www.cga.ct.gov/asp/menu/hlist.asp")
            request.assert_not_called()

    def test_verified_roster_transport_rejects_http_redirect_content(self):
        class Response:
            url = "http://legislature.example.gov/roster"
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return None
            def read(self):
                raise AssertionError("unverified content must not be consumed")
        with patch.object(p, "https_open", return_value=Response()):
            with self.assertRaises(ValueError):
                p.roster_fetch("https://legislature.example.gov/roster")

    def test_https_http_https_redirect_is_rejected_before_plaintext_request(self):
        calls = []
        class Secure(p.urllib.request.HTTPSHandler):
            def https_open(self, req):
                calls.append(req.full_url)
                response = p.urllib.response.addinfourl(io.BytesIO(b""), {"location": "http://plain.example/hop"}, req.full_url, 302)
                response.msg = "Found"
                return response
        class Plain(p.urllib.request.HTTPHandler):
            def http_open(self, req):
                raise AssertionError("plaintext hop must never be requested, even if it redirects back to HTTPS")
        with patch.object(p.urllib.request, "HTTPSHandler", Secure), patch.object(p.urllib.request, "HTTPHandler", Plain):
            with self.assertRaisesRegex(ValueError, "non-HTTPS redirect"):
                p.fetch("https://secure.example/start")
        self.assertEqual(calls, ["https://secure.example/start"])

    def test_additional_legiscan_requests_have_a_shared_daily_cap(self):
        directory = f"{p.DATA}/state/sponsor-cache"
        p.write(directory + "/budget.json", json.dumps({"day": time.strftime("%Y-%m-%d", time.gmtime()), "queries": 200}))
        read = Mock(return_value={"ok": True})
        self.assertIsNone(p.sponsor_cache("legiscan:test", read))
        read.assert_not_called()
        self.assertEqual(p.sponsor_cache("roster:public", read), {"ok": True})

    def test_current_csv_requires_freshness_valid_ids_and_live_role(self):
        now = time.time()
        fields = ["id", "name", "sources", "links", "current_chamber", "death_date"]
        rows = [PERSON_ROW, {**PERSON_ROW, "id": OTHER, "death_date": "2025-01-01"},
                {**PERSON_ROW, "id": "~name"}, {**PERSON_ROW, "id": OTHER, "current_chamber": ""}]
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        class Response(io.BytesIO):
            headers = {"Last-Modified": email.utils.formatdate(now, usegmt=True)}
        with patch.object(p, "https_open", side_effect=lambda *a, **kw: Response(stream.getvalue().encode())):
            self.assertEqual([r["id"] for r in p.state_people("AL")], [PERSON])
            with patch.object(p.time, "time", return_value=now + 4 * 86400):
                self.assertEqual([r["id"] for r in p.state_people("AL")], [PERSON])
            with patch.object(p.time, "time", return_value=now + 7 * 86400 + 1):
                self.assertEqual(p.state_people("AL"), [])
        self.assertEqual(p.state_people("ZZ"), [])


if __name__ == "__main__":
    unittest.main()
