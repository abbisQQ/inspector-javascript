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


class KatanaRoutesTests(unittest.TestCase):
    def test_keeps_in_scope_routes_skips_assets_and_offsite(self):
        with tempfile.TemporaryDirectory() as d:
            kf = Path(d) / "katana-out.txt"
            kf.write_text("\n".join([
                "http://t.com/dashboard",
                "http://t.com/research?url=https://evil.com",   # query route kept (path in scope)
                "http://t.com/vendor/jquery.min.js",            # asset skipped
                "http://t.com/style.css",                       # asset skipped
                "http://t.com/dashboard",                       # dup collapsed
                "https://other.com/x",                          # off-site skipped
                "http://t.com/allocations/4#frag",              # fragment stripped
                "",                                             # blank skipped
            ]))
            routes = ijs._katana_routes(kf, "t.com")
        self.assertIn("http://t.com/dashboard", routes)
        self.assertIn("http://t.com/research?url=https://evil.com", routes)
        self.assertIn("http://t.com/allocations/4", routes)
        self.assertNotIn("http://t.com/vendor/jquery.min.js", routes)
        self.assertNotIn("http://t.com/style.css", routes)
        self.assertFalse(any("other.com" in r for r in routes))
        self.assertEqual(len(routes), len(set(routes)))       # deduped

    def test_missing_file_returns_empty(self):
        self.assertEqual(ijs._katana_routes(Path("/no/such/file.txt"), "t.com"), [])


class InfraPatternTests(unittest.TestCase):
    def _match(self, kind, text):
        pat = {k: p for p, k in ijs.INFRA_PATTERNS}[kind]
        m = pat.search(text)
        return (m.group(1) if (m and kind == "api_base_url") else (m.group(0) if m else None))

    def test_private_ips(self):
        self.assertTrue(self._match("internal_ip", "connect 10.0.3.14:8080"))
        self.assertTrue(self._match("internal_ip", "192.168.1.5"))
        self.assertTrue(self._match("internal_ip", "172.16.0.99"))

    def test_internal_ip_no_false_positive_on_public_or_netmask(self):
        self.assertIsNone(self._match("internal_ip", "version 11.22.33.44"))
        self.assertIsNone(self._match("internal_ip", "9.10.11.12 build"))
        self.assertIsNone(self._match("internal_ip", "mask 255.255.255.0"))

    def test_cloud_metadata_and_mesh(self):
        self.assertTrue(self._match("cloud_metadata", "http://169.254.169.254/latest"))
        self.assertTrue(self._match("service_mesh", "payments.prod.svc.cluster.local"))
        self.assertTrue(self._match("service_mesh", "redis.service.consul"))

    def test_internal_host_and_api_base(self):
        self.assertTrue(self._match("internal_host", "https://api.corp.internal/v1"))
        self.assertEqual(self._match("api_base_url", "const API_BASE_URL = 'https://api.x/v2'"),
                         "https://api.x/v2")


class FrameworkRouteTests(unittest.TestCase):
    def test_nextjs_pages_router(self):
        text = ('self.__BUILD_MANIFEST={"/":["a"],"/login":["b"],'
                '"/api/users/[id]":["c"],"/_error":["e"],'
                'sortedPages:["/","/login","/api/users/[id]","/_app"]};'
                'self.__BUILD_MANIFEST_CB&&self.__BUILD_MANIFEST_CB()')
        fw, routes = ijs.extract_framework_routes(text)
        self.assertEqual(fw, "nextjs")
        self.assertEqual(routes, {"/", "/login", "/api/users/[id]"})   # /_error, /_app dropped

    def test_nextjs_app_router(self):
        text = ('{"pages":{"/page":["c1"],"/about/page":["c2"],'
                '"/api/users/route":["c3"],"/dashboard/(admin)/settings/page":["c4"],'
                '"/layout":["l"],"/not-found":["n"]}}')
        fw, routes = ijs.extract_framework_routes(text)
        self.assertEqual(fw, "nextjs")
        self.assertEqual(routes, {"/", "/about", "/api/users", "/dashboard/settings"})
        self.assertNotIn("/page", routes)       # suffix stripped, not left raw
        self.assertNotIn("/layout", routes)     # non-route entry dropped

    def test_nuxt_routes(self):
        text = ('window.__NUXT__={};'
                'const r=[{path:"/",component:a},{path:"/products/:id",component:b},'
                '{path:"/checkout",component:c}]')
        fw, routes = ijs.extract_framework_routes(text)
        self.assertEqual(fw, "nuxt")
        self.assertEqual(routes, {"/", "/products/:id", "/checkout"})

    def test_plain_js_no_false_positive(self):
        self.assertEqual(ijs.extract_framework_routes('const x={"/foo":1};fetch("/api/y")'),
                         (None, set()))


class RouteMutationTests(unittest.TestCase):
    def test_generates_parents_siblings_and_wordlist(self):
        muts = ijs.generate_route_mutations(["/api/v1/users/42", "/api/v1/orders"])
        # parents
        self.assertIn("/api/v1/users", muts)
        self.assertIn("/api/v1", muts)
        self.assertIn("/api", muts)
        # wordlist under a prefix and under a collection
        self.assertIn("/api/v1/health", muts)
        self.assertIn("/api/v1/orders/count", muts)
        # singular/plural
        self.assertIn("/api/v1/user", muts)

    def test_excludes_dynamic_segments_and_known(self):
        muts = ijs.generate_route_mutations(["/api/users/[id]", "/x/:slug", "/y/EXPR", "/health"])
        self.assertFalse(any(tok in m for m in muts for tok in ("[", ":", "EXPR")))
        self.assertNotIn("/health", muts)   # a seed itself is never echoed back

    def test_cap_is_respected(self):
        seeds = [f"/api/resource{i}/item" for i in range(50)]
        self.assertLessEqual(len(ijs.generate_route_mutations(seeds, cap=120)), 120)


class VendorFileAndNoiseTests(unittest.TestCase):
    def test_vendor_files_detected(self):
        for n in ["34115704_react.development.js", "5c2dd099_react-dom.development.js",
                  "e6243397_angular_1-7-7.js", "vendors.abc.js", "runtime.123.js",
                  "chunk-vendors.js", "jquery.min.js", "polyfills.js"]:
            self.assertTrue(ijs._looks_like_vendor_file(n), n)

    def test_app_files_not_misclassified(self):
        for n in ["main.7c8cb9a3.js", "app.bundle.js", "index.js", "dashboard.js",
                  "subscribeNow.js", "components__emails__EmailComposer.js"]:
            self.assertFalse(ijs._looks_like_vendor_file(n), n)

    def test_noise_markers_cover_tooling_comments(self):
        for s in ["// eslint-disable-next-line react-internal/no-production-logging",
                  "// $FlowFixMe only called in DEV", "/* prettier-ignore */",
                  "// @ts-ignore", "/* istanbul ignore next */"]:
            self.assertTrue(any(mark in s.lower() for mark in ijs._COMMENT_NOISE_MARKERS), s)

    def test_high_value_kinds_kept_even_in_vendor(self):
        # a leaked internal IP matters wherever it appears
        self.assertIn("internal_ip", ijs._HIGH_VALUE_COMMENT_KINDS)
        self.assertIn("api_base_url", ijs._HIGH_VALUE_COMMENT_KINDS)
        self.assertNotIn("todo", ijs._HIGH_VALUE_COMMENT_KINDS)


class CrossOriginScriptTests(unittest.TestCase):
    def test_app_cdn_bundles_are_kept(self):
        for u in ["https://newsquad.b-cdn.net/assets/js/app.min.js",
                  "https://d123.cloudfront.net/app.bundle.js",
                  "https://cdn.jsdelivr.net/npm/jquery@3/dist/jquery.min.js",
                  "https://assets.example.org/main.js"]:
            self.assertFalse(ijs._is_third_party_script(u), u)   # kept = not third-party

    def test_trackers_ads_widgets_are_dropped(self):
        for u in ["https://www.google-analytics.com/analytics.js",
                  "https://www.googletagmanager.com/gtag/js?id=X",
                  "https://connect.facebook.net/en_US/fbevents.js",
                  "https://www.google.com/recaptcha/api.js",
                  "https://widget.intercom.io/widget/abc",
                  "https://static.hotjar.com/c/hotjar.js"]:
            self.assertTrue(ijs._is_third_party_script(u), u)


class LineLookupTests(unittest.TestCase):
    def test_matches_naive_count(self):
        import random
        for _ in range(100):
            content = "".join(random.choice("ab\n\nx ") for _ in range(300))
            line_of = ijs.make_line_lookup(content)
            for pos in range(0, len(content) + 1, 5):
                self.assertEqual(line_of(pos), content[:pos].count("\n") + 1)

    def test_basics(self):
        line_of = ijs.make_line_lookup("a\nbb\nccc")
        self.assertEqual(line_of(0), 1)    # 'a'
        self.assertEqual(line_of(2), 2)    # first 'b'
        self.assertEqual(line_of(5), 3)    # first 'c'

    def test_empty_content(self):
        self.assertEqual(ijs.make_line_lookup("")(0), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
