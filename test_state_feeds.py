"""Keyed native-feed routing, source cutover and document/transport checks; no network or Docker."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import types
import unittest
from unittest.mock import patch

import plaingov as app


class StateFeedsTest(unittest.TestCase):
    def test_keyed_feed_routing_and_cutover(self):
        for st, key in (("IN", "INDIANA_API_KEY"), ("VA", "VA_API_KEY")):
            with self.subTest(st=st), tempfile.TemporaryDirectory() as data:
                raw = {"identifier": "HB 1", "legislative_session": "2026", "classification": ["bill"],
                       "actions": [{"date": "2026-02-02", "classification": ["became-law"],
                                    "organization_id": '~{"classification": "executive"}'}],
                       "sources": [{"url": "https://example.gov/bill/1"}]}
                files = Path(data) / f"scrape/{st.lower()}/{st.lower()}"
                files.mkdir(parents=True)
                (files / "bill_1.json").write_text(json.dumps(raw))
                page = f"states/{st.lower()}/2026-hb0001"
                seen_path = f"{data}/state/states-{st.lower()}.json"
                original = [f"{st}-ls123-4", "unrelated-history"]
                app.write(seen_path, json.dumps(original))
                rec = {"id": page, "state": st, "date": "2026-02-02", "stage": app.state_stage(st, 4),
                       "summary": "Keep my prose", "sponsor_legiscan_id": 42}
                app.write(f"{data}/bills/{page}.json", json.dumps(rec))
                app.write(f"{data}/state/queue.json", '[{"key":"old-queue"}]')
                for k in ("queue.json", "posts.json"):
                    p = Path(data) / "state" / k
                    if not p.exists(): p.write_text('["keep"]')
                state_before = {p.name: p.read_bytes() for p in (Path(data) / "state").glob('*.json')}
                calls = []

                def run(cmd, **kwargs):
                    calls.append(cmd)
                    return types.SimpleNamespace(returncode=0, stdout="", stderr="")

                def process(feed, items, update):
                    # Exercise the real routing/seed path, without AI, posts or site builds.
                    items = list(items)
                    self.assertEqual(update, app.state_update)
                    seen = app.load(seen_path, [])
                    native_key = f"{st}-2026-HB 1-4"
                    self.assertEqual(items[0][0], native_key)
                    self.assertEqual(native_key in seen, unchanged)
                    self.assertTrue(set(original) <= set(seen))
                    return 0, 0, 0

                with patch.dict(os.environ, {key: "secret", "NEW_YORK_API_KEY": "unrelated", "LEGISCAN_API_KEY": "backup"}), \
                     patch.object(app, "DATA", data), patch.object(app, "HEALTH", f"{data}/state/health.json"), \
                     patch.object(app, "LEGISCAN_STATES", []), patch.object(app, "ai_stages", return_value={}), \
                     patch.object(app.subprocess, "run", side_effect=run), patch.object(app, "process", side_effect=process), \
                     contextlib.redirect_stdout(io.StringIO()):
                    with patch.object(app, "legiscan_state") as fallback, patch.dict(os.environ, {key: ""}):
                        app.scrape_state(st)
                        fallback.assert_called_once_with(st)
                    for unchanged, source, changes in ((True, "LegiScan", {}),
                            (False, "LegiScan", {"date": "2026-01-01"}),
                            (False, "LegiScan", {"stage": app.state_stage(st, 2, "lower")}),
                            (False, "legislature", {})):
                        app.write(seen_path, json.dumps(original))
                        app.set_health(st, source=source, status="ok")
                        app.write(f"{data}/bills/{page}.json", json.dumps({**rec, **changes}))
                        before = Path(f"{data}/bills/{page}.json").read_bytes()
                        health = app.scrape_state(st)
                        self.assertEqual(health["source"], "legislature")
                        self.assertEqual(Path(f"{data}/bills/{page}.json").read_bytes(), before)
                    box = next(cmd for cmd in calls if "--name" in cmd)
                    self.assertIn(key, box)
                    self.assertNotIn("secret", box)
                    self.assertNotIn("NEW_YORK_API_KEY", box)
                    self.assertNotIn("LEGISCAN_API_KEY", box)
                    self.assertEqual(app.state_id({**raw, "state": st}), page)
                    self.assertEqual(app.legiscan_id({"state": st, "number": "HB0001", "url": "https://legiscan.com/IN/bill/HB0001/2026"}), page)
                    self.assertNotEqual(app.state_id({**raw, "state": st, "legislative_session": "2027"}), page)
                    self.assertEqual(Path(f"{data}/state/queue.json").read_bytes(), state_before['queue.json'])
                    self.assertEqual(Path(f"{data}/state/posts.json").read_bytes(), state_before['posts.json'])
                    self.assertIs(app.state_feed(st)[1], app.state_update)
                    with patch.dict(os.environ, {key: ""}), patch.object(app, "legiscan_items", return_value=[]):
                        self.assertIs(app.state_feed(st)[1], app.legiscan_update)
                    app.write(f"{data}/bills/states/{st.lower()}/2026-hb-1.json", json.dumps(rec))
                    with self.assertRaisesRegex(ValueError, "Ambiguous"):
                        app.state_id({**raw, "state": st})

    def test_indiana_document(self):
        url = "https://iga.in.gov/pdf-documents/124/2026/house/bills/HB1001/HB1001.06.ENRS.pdf"
        response = types.SimpleNamespace(read=lambda: b"%PDF-real", headers=types.SimpleNamespace(get_content_charset=lambda: None))
        with patch.object(app, "https_open", return_value=contextlib.nullcontext(response)) as fetch, \
             patch.object(app, "doc_bytes_text", return_value="Bill text") as parse:
            self.assertEqual(app.doc_text(url), "Bill text")
            self.assertEqual(fetch.call_args.args[0].get_header("User-agent"), f"Mozilla/5.0 {app.UA}")
            self.assertIsNone(fetch.call_args.args[0].get_header("X-api-key"))
            parse.assert_called_once_with(b"%PDF-real", None)
            response.read = lambda: b"<!doctype html><p>App shell</p>"
            with self.assertRaisesRegex(ValueError, "non-PDF"):
                app.doc_text(url)
            app.doc_text("https://example.gov/bill")
            self.assertEqual(fetch.call_args.args[0].get_header("User-agent"), app.UA)

    def test_container_tls_hook(self):
        class HTTPAdapter:
            def send(self, request, **kwargs):
                return kwargs["verify"]

        modules = {"scrapelib": types.ModuleType("scrapelib"), "openstates": types.ModuleType("openstates"),
                   "requests.adapters": types.ModuleType("requests.adapters")}
        modules["scrapelib"]._user_agent = "upstream"
        modules["openstates"].settings = types.SimpleNamespace()
        modules["requests.adapters"].HTTPAdapter = HTTPAdapter
        with patch.dict("sys.modules", modules):
            runpy.run_path(str(Path(__file__).parent / "scrapers/sitecustomize.py"))
        adapter = HTTPAdapter()
        def request(url):
            return adapter.send(types.SimpleNamespace(url=url, headers={}), verify=False)
        for host in ("api.iga.in.gov", "iga.in.gov", "lis.virginia.gov"):
            self.assertIs(request(f"https://{host}/"), True)
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                request(f"http://{host}/")
        self.assertIs(request("https://other.gov/"), False)
        self.assertIs(request("https://api.iga.in.gov.attacker.test/"), False)
        with patch.dict(os.environ, {"INDIANA_API_KEY": "in-secret", "VA_API_KEY": "va-secret"}):
            discovery = types.SimpleNamespace(url="https://lis.virginia.gov/Session/api/GetSessionListAsync/", headers={"WebAPIKey": "upstream-web-key"})
            self.assertIs(adapter.send(discovery, verify=False), True)
            self.assertEqual(discovery.headers["WebAPIKey"], "va-secret")
            for header, value, origin in (("x-api-key", "in-secret", "api.iga.in.gov"), ("WebAPIKey", "va-secret", "lis.virginia.gov")):
                req = types.SimpleNamespace(url=f"https://{origin}/", headers={header: value})
                self.assertIs(adapter.send(req, verify=False), True)
                for url in ("https://attacker.test/", f"http://{origin}/"):
                    req.url = url
                    with self.assertRaisesRegex(ValueError, "credential"):
                        adapter.send(req, verify=False)


if __name__ == "__main__":
    unittest.main()
