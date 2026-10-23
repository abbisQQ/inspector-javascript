"""Unit tests for Inspector-Javascript's pure helpers.

Dependency-free: run with the stdlib test runner (no pytest needed):

    python3 -m unittest discover -s tests -v

or, if you have pytest:

    python3 -m pytest tests/
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

# Load the hyphenated main module (inspector-js.py can't be imported by name).
_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("inspector_js", _ROOT / "inspector-js.py")
ijs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ijs)


class ScopeTests(unittest.TestCase):
    def test_host_matches_exact_and_subdomain(self):
        self.assertTrue(ijs._host_matches("api.example.com", "example.com"))
        self.assertTrue(ijs._host_matches("example.com", "example.com"))
        self.assertFalse(ijs._host_matches("evil.com", "example.com"))
        self.assertFalse(ijs._host_matches("notexample.com", "example.com"))
        self.assertFalse(ijs._host_matches("", "example.com"))

    def test_is_in_scope(self):
        self.assertTrue(ijs.is_in_scope("/api/users", "example.com"))        # relative → in scope
        self.assertTrue(ijs.is_in_scope("https://example.com/x", "example.com"))
        self.assertTrue(ijs.is_in_scope("https://api.example.com/x", "example.com"))
        self.assertFalse(ijs.is_in_scope("https://evil.com/x", "example.com"))
        self.assertFalse(ijs.is_in_scope("mailto:a@b.com", "example.com"))
        self.assertFalse(ijs.is_in_scope("", "example.com"))

    def test_host_in_scope_requires_absolute(self):
        self.assertTrue(ijs.host_in_scope("https://example.com/a", "example.com"))
        self.assertFalse(ijs.host_in_scope("/relative", "example.com"))  # not absolute → False


class SoftFourOhFourTests(unittest.TestCase):
    def test_catch_all_detected_when_probes_agree(self):
        sigs = ijs.soft404_signatures(
            lambda u: {"status": 200, "size": 9903, "content_type": "text/html"},
            "https://t.com")
        self.assertEqual(sigs, {(200, 9903, "text/html")})

    def test_honest_404_yields_no_signature(self):
        sigs = ijs.soft404_signatures(
            lambda u: {"status": 404, "size": 120, "content_type": "text/html"},
            "https://t.com")
        self.assertEqual(sigs, set())

    def test_inconsistent_responses_yield_no_signature(self):
        seq = iter([
            {"status": 200, "size": 100, "content_type": "text/html"},
            {"status": 200, "size": 900, "content_type": "text/html"},
            {"status": 500, "size": 0, "content_type": ""},
        ])
        self.assertEqual(ijs.soft404_signatures(lambda u: next(seq), "https://t.com"), set())

    def test_is_soft404_matching_and_tolerance(self):
        sigs = {(200, 9903, "text/html")}
        self.assertTrue(ijs.is_soft404({"status": 200, "size": 9903, "content_type": "text/html"}, sigs))
        self.assertTrue(ijs.is_soft404({"status": 200, "size": 9950, "content_type": "text/html"}, sigs))  # within tolerance
        self.assertFalse(ijs.is_soft404({"status": 200, "size": 26, "content_type": "application/json"}, sigs))
        self.assertFalse(ijs.is_soft404({"status": 401, "size": 9903, "content_type": "text/html"}, sigs))  # 4xx never soft
        self.assertFalse(ijs.is_soft404({"status": 200, "size": 9903, "content_type": "text/html"}, set()))


class ResolveReferencedUrlTests(unittest.TestCase):
    def test_leading_placeholder_resolves_to_origin(self):
        self.assertEqual(ijs._resolve_referenced_url("${env.api}/assets/config.json", "https://t.com", "t.com"),
                         "https://t.com/assets/config.json")
        self.assertEqual(ijs._resolve_referenced_url("EXPR/rest/languages", "https://t.com", "t.com"),
                         "https://t.com/rest/languages")

    def test_rooted_and_absolute(self):
        self.assertEqual(ijs._resolve_referenced_url("/api/c.json", "https://t.com", "t.com"),
                         "https://t.com/api/c.json")
        self.assertEqual(ijs._resolve_referenced_url("//t.com/c.json", "https://t.com", "t.com"),
                         "https://t.com/c.json")

    def test_out_of_scope_and_midpath_placeholder_rejected(self):
        self.assertIsNone(ijs._resolve_referenced_url("https://evil.com/c.json", "https://t.com", "t.com"))
        self.assertIsNone(ijs._resolve_referenced_url("/rest/basket/${id}/x.json", "https://t.com", "t.com"))
        self.assertIsNone(ijs._resolve_referenced_url("", "https://t.com", "t.com"))


class OpenApiTests(unittest.TestCase):
    def test_is_openapi_doc(self):
        self.assertTrue(ijs._is_openapi_doc({"openapi": "3.0.0", "paths": {}}))
        self.assertTrue(ijs._is_openapi_doc({"swagger": "2.0", "paths": {}}))
        self.assertTrue(ijs._is_openapi_doc({"paths": {"/a": {}}}))
        self.assertFalse(ijs._is_openapi_doc({"foo": "bar"}))
        self.assertFalse(ijs._is_openapi_doc([1, 2, 3]))

    def test_extract_endpoints_with_server_prefix_and_params(self):
        doc = {
            "openapi": "3.0.0",
            "servers": [{"url": "https://api.x/v1"}],
            "paths": {
                "/users": {
                    "get": {"parameters": [{"in": "query", "name": "q"}]},
                    "post": {"requestBody": {"content": {"application/json": {}}}},
                },
            },
        }
        eps = ijs.extract_openapi_endpoints(doc, "doc-url")
        by_method = {e["method"]: e for e in eps}
        self.assertEqual(set(by_method), {"GET", "POST"})
        self.assertEqual(by_method["GET"]["url"], "https://api.x/v1/users")
        self.assertIn("q", by_method["GET"]["queryParams"])
        self.assertIn("body", by_method["POST"]["bodyParams"])

    def test_basepath_swagger2(self):
        doc = {"swagger": "2.0", "basePath": "/api", "paths": {"/ping": {"get": {}}}}
        eps = ijs.extract_openapi_endpoints(doc, "d")
        self.assertEqual(eps[0]["url"], "/api/ping")
        self.assertEqual(eps[0]["type"], "swagger")


class JsonUnwrapTests(unittest.TestCase):
    def test_unwrap_module_exports(self):
        self.assertEqual(ijs._unwrap_js_json('module.exports = {"a":1};'), '{"a":1}')
        self.assertEqual(ijs._unwrap_js_json('export default {"a":1}'), '{"a":1}')
        self.assertEqual(ijs._unwrap_js_json('{"a":1}'), '{"a":1}')
        self.assertEqual(ijs._unwrap_js_json('﻿{"a":1}'), '{"a":1}')


class NpmClassifyTests(unittest.TestCase):
    def test_public_vs_private(self):
        self.assertTrue(ijs._is_public_npm_package("react"))
        self.assertTrue(ijs._is_public_npm_package("@babel/runtime"))
        self.assertTrue(ijs._is_public_npm_package("babel"))
        self.assertTrue(ijs._is_public_npm_package("react-foo"))      # public prefix
        self.assertFalse(ijs._is_public_npm_package("nls"))           # internal
        self.assertFalse(ijs._is_public_npm_package("@acme/ui"))      # private scope


class BurpParseTests(unittest.TestCase):
    def test_classic_request_line(self):
        raw = "GET /api/users?x=1 HTTP/1.1\r\nHost: t.com\r\nCookie: s=abc\r\nAccept: */*\r\n\r\n"
        out = ijs._parse_raw_http_request(raw)
        self.assertEqual(out["url"], "https://t.com/api/users?x=1")
        self.assertEqual(out["method"], "GET")
        self.assertIn("Cookie: s=abc", out["headers"])
        self.assertNotIn("Accept: */*", out["headers"])   # noise header stripped

    def test_http2_pseudo_headers(self):
        raw = ":method: POST\n:path: /x\n:authority: h.com\nCookie: a=b\n"
        out = ijs._parse_raw_http_request(raw)
        self.assertEqual(out["url"], "https://h.com/x")
        self.assertEqual(out["method"], "POST")
        self.assertIn("Cookie: a=b", out["headers"])

    def test_missing_host_raises(self):
        with self.assertRaises(ValueError):
            ijs._parse_raw_http_request("GET /x HTTP/1.1\r\n\r\n")


class ParseHeadersTests(unittest.TestCase):
    def test_default_user_agent_applied(self):
        d = ijs.parse_headers([])
        self.assertEqual(d.get("User-Agent"), ijs.HTTP_USER_AGENT)

    def test_explicit_headers_parsed(self):
        d = ijs.parse_headers(["Cookie: a=b", "X-Test: 1"])
        self.assertEqual(d.get("Cookie"), "a=b")
        self.assertEqual(d.get("X-Test"), "1")


class RetireSummaryTests(unittest.TestCase):
    def test_dedupe_across_locations(self):
        entries = [
            {"file": "a.js", "results": [
                {"component": "jquery", "version": "2.2.4",
                 "vulnerabilities": [{"identifiers": {"CVE": ["CVE-1"]}}]}]},
            {"file": "b.js", "results": [   # same lib+vuln in another file
                {"component": "jquery", "version": "2.2.4",
                 "vulnerabilities": [{"identifiers": {"CVE": ["CVE-1"]}}]}]},
        ]
        libs, affected, vulns = ijs._retire_summary(entries)
        self.assertEqual(libs, {"jquery@2.2.4"})
        self.assertEqual(affected, {"jquery@2.2.4"})
        self.assertEqual(len(vulns), 1)   # not double-counted


class TemplateLiteralTests(unittest.TestCase):
    def test_extracts_templated_endpoint(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.js"
            p.write_text("const u = `/api/items?id=${id}`; fetch(u);")
            recs = ijs._template_literal_endpoints([str(p)], set())
        urls = [r["url"] for r in recs]
        self.assertIn("/api/items?id=EXPR", urls)


class CollectLocalSourcesTests(unittest.TestCase):
    def test_keeps_internal_skips_public_and_mirrors_tree(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "src"
            (root / "a").mkdir(parents=True)
            (root / "node_modules" / "react").mkdir(parents=True)
            (root / "node_modules" / "@acme" / "ui").mkdir(parents=True)
            (root / "a" / "app.js").write_text("fetch('/x')")
            (root / "cfg.json").write_text('{"k":1}')
            (root / "node_modules" / "react" / "index.js").write_text("var r=1")
            (root / "node_modules" / "@acme" / "ui" / "i.ts").write_text("export const k=1")
            out = Path(d) / "out"
            res = ijs.collect_local_sources(root, out / "js-clean", out / "bundles")
            # internal @acme/ui kept, public react skipped
            self.assertIn("@acme/ui", res["first_party_pkgs"])
            self.assertIn("react", res["public_pkgs"])
            # json collected as an extra, tree structure preserved
            self.assertTrue((out / "js-clean" / "a" / "app.js").exists())
            self.assertTrue((out / "js-clean" / "cfg.json").exists())
            self.assertFalse((out / "js-clean" / "node_modules" / "react" / "index.js").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
