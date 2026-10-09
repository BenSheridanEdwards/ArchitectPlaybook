from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "security-audit" / "scripts" / "collect.py"

for _variable in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_COMMON_DIR"):
    os.environ.pop(_variable, None)

spec = importlib.util.spec_from_file_location("security_collector", COLLECTOR)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = collector
spec.loader.exec_module(collector)

protocol_spec = importlib.util.spec_from_file_location(
    "audit_run_for_security_collector_tests", ROOT / "audit-protocol" / "scripts" / "audit_run.py"
)
assert protocol_spec and protocol_spec.loader
protocol = importlib.util.module_from_spec(protocol_spec)
sys.modules[protocol_spec.name] = protocol
protocol_spec.loader.exec_module(protocol)

CHECK = "security-audit."
SECRETS = CHECK + "no-secrets-in-source"
IGNORED = CHECK + "env-files-gitignored"
DYNAMIC = CHECK + "no-dynamic-code-execution"

# Fake credentials, assembled at run time so this public repository never holds
# a string in a real provider's format. None of them is a live credential.
STRIPE = "sk_" + "live_" + "51HqFixtureOnly" + "NotARealKey0000"
AWS = "AK" + "IA" + "Q3EGWTZ7R5XL2M4N"
GITHUB = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8"
SLACK = "xo" + "xb-" + "2048-1024-" + "AbCdEfGhIjKlMnOp"
OPENAI = "sk-" + "proj-" + "Q1w2E3r4T5y6U7i8O9p0" + "A1s2D3f4G5h6J7k8L9z0X1c2V3b4"
ANTHROPIC = "sk-" + "ant-api03-" + "Qw3rTy7UiOp1AsDf5GhJk9LzXc2VbNm4" * 3
NPM = "np" + "m_" + "a1B2c3D4e5F6g7H8i9J0" + "k1L2m3N4o5P6q7R8"
DATABASE_PASSWORD = "Pr0d" + "Passw0rd" + "Zebra42"
PRIVATE_KEY_HEADER = "-----BEGIN RSA " + "PRIVATE KEY-----"
PRIVATE_KEY_BODY = "".join(("MIIEow", "IBAAKC", "AQEAu1", "SU1L7V", "LPHCgc", "BIjSnT", "3fHxD2", "pNk8qY", "0r1Wv5", "Lz9QmB", "7cT4sX", "e6Ja"))
GENERIC_PASSWORD = "".join(("q8Zt3L", "w9Rv2X", "n5Kp7Y", "d4Mh"))
LONG_PASSWORD = "".join(("Hq4Tz8", "Wm2Yk6", "Nb9Pr3", "Xc7Lf5", "Jd1"))
HEX_PASSWORD = "".join(("9f3a1c", "7e5b2d", "8f4a6c", "0e9b1d", "3f5a7c", "2e"))
WORD_PASSWORD = "".join(("correct", "horse", "battery", "staple"))
PUNCTUATED_PASSWORD = "".join(("Kx7", "$mQ2", "{vL9", "<pR4", "*tW"))
SHORT_PASSWORD = "".join(("ab", "cde"))
PLAIN_KEY_BODY = "".join(("abcdefgh", "ijklmnop", "qrstuvwx", "yzabcdef", "ghijklmn", "opqrst"))
DOLLAR_PASSWORD = "".join(("$Correct", "Horse", "Battery", "Staple42!"))
ENV_SECRET = "s3cr3t" + "ValueForTheFixture99"


class SecurityCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.root = base / "repository"
        self.root.mkdir()
        # A personal or global ignore file must not change what the collector sees.
        (base / "xdg").mkdir()
        patcher = mock.patch.dict(
            os.environ, {"XDG_CONFIG_HOME": str(base / "xdg"), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        protocol.clear_caches()

    def git(self, *arguments: str) -> None:
        subprocess.run(["git", "-C", str(self.root), *arguments], check=True, capture_output=True)

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message: str = "change") -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def collect(self, *enrichment: str) -> dict[str, Any]:
        protocol.clear_caches()
        return collector.collect(self.root, list(enrichment))

    def assert_verifies(self, result: dict[str, Any]) -> None:
        """Every evidence entry passes the protocol's own verification, and nothing looks like a secret."""
        for check_id, outcome in result["checks"].items():
            for entry in outcome.get("evidence", []):
                with self.subTest(check=check_id, entry=entry):
                    protocol.verify_evidence(self.root, entry)
        self.assertFalse(protocol.contains_secret(json.dumps(result)), "output contains secret-looking text")

    def assert_no_values(self, result: dict[str, Any], *values: str) -> None:
        text = json.dumps(result)
        for value in values:
            self.assertNotIn(value, text)
            self.assertNotIn(value[-12:], text)

    # ----------------------------------------------------------------- secrets in tracked files

    def test_a_committed_key_is_cited_with_the_value_redacted(self) -> None:
        self.write("lib/payments.ts", f"import Stripe from 'stripe';\n\nexport const stripe = new Stripe('{STRIPE}', {{}});\n")
        self.commit()
        result = self.collect()
        secrets = result["checks"][SECRETS]
        self.assertEqual(secrets["status"], "violation")
        self.assertEqual(secrets["judgement"], "act-on")
        self.assertEqual(secrets["evidence"], ["lib/payments.ts:3 — stripe-live-secret-key: `sk_live_<REDACTED>`"])
        self.assert_verifies(result)
        self.assert_no_values(result, STRIPE)

    def test_every_credential_format_is_found_verified_and_never_echoed(self) -> None:
        samples = {
            "aws-access-key-id": f"const accessKeyId = '{AWS}';",
            "github-token": f"headers: {{ authorization: `token {GITHUB}` }},",
            "slack-token": f"const slack = new WebClient(\"{SLACK}\");",
            "openai-api-key": f"apiKey: '{OPENAI}',",
            "anthropic-api-key": f"new Anthropic({{ apiKey: '{ANTHROPIC}' }})",
            "npm-token": f"//registry.npmjs.org/:_authToken={NPM}",
            "connection-string-password": f"url: 'postgresql://app:{DATABASE_PASSWORD}@db.prod.internal:5432/app',",
            "private-key": f"{PRIVATE_KEY_HEADER}\n{PRIVATE_KEY_BODY}",
        }
        for index, (rule, line) in enumerate(samples.items()):
            self.write(f"config/{index}.ts", f"// {rule}\n{line}\n")
        self.commit()
        result = self.collect()
        secrets = result["checks"][SECRETS]
        self.assertEqual(secrets["status"], "violation")
        self.assertEqual(result["snapshot"]["secretScan"]["findings"], {rule: 1 for rule in samples})
        for rule in samples:
            self.assertTrue(any(f" — {rule}" in entry for entry in secrets["evidence"]), rule)
        self.assert_verifies(result)
        self.assert_no_values(result, AWS, GITHUB, SLACK, OPENAI, ANTHROPIC, NPM, DATABASE_PASSWORD)

    def test_several_secrets_on_one_line_become_one_verified_citation(self) -> None:
        self.write("src/keys.ts", f"export const keys = ['{AWS}', '{GITHUB}'];\n")
        self.commit()
        result = self.collect()
        evidence = result["checks"][SECRETS]["evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertIn("aws-access-key-id, github-token", evidence[0])
        self.assertTrue(evidence[0].endswith("`AKIA<REDACTED>`"))
        self.assert_verifies(result)

    def test_backticks_and_spaces_in_paths_still_verify(self) -> None:
        self.write("src/my folder/client.ts", f"const auth = `Bearer {GITHUB}`;\n")
        self.commit()
        result = self.collect()
        self.assertTrue(result["checks"][SECRETS]["evidence"][0].startswith("`src/my folder/client.ts`:1 — "))
        self.assert_verifies(result)

    def test_placeholders_documentation_values_and_local_databases_are_not_secrets(self) -> None:
        self.write(
            "src/config.ts",
            "export const aws = 'AKIA" + "IOSFODNN7EXAMPLE';\n"
            "export const stripe = 'sk_" + "live_" + "x" * 24 + "';\n"
            "export const database = 'postgres://postgres:" + "devpassword123@localhost:5432/app';\n"
            "export const compose = 'postgres://app:" + "devpassword123@db:5432/app';\n"
            "export const template = 'postgres://app:${DB_PASSWORD}@prod.example.net/app';\n",
        )
        self.commit()
        result = self.collect()
        self.assertEqual(result["checks"][SECRETS]["status"], "present")
        self.assert_verifies(result)

    def test_only_tracked_files_are_scanned_for_secrets(self) -> None:
        self.write("src/a.ts", "export const a = 1;\n")
        self.commit()
        self.write("scratch.ts", f"const token = '{GITHUB}';\n")
        result = self.collect()
        self.assertEqual(result["checks"][SECRETS]["status"], "present")
        self.assertIn("in 1 tracked text files", result["checks"][SECRETS]["evidence"][0])

    def test_binary_files_are_skipped(self) -> None:
        (self.root / "image.png").write_bytes(b"\x89PNG\0\0" + GITHUB.encode())
        self.commit()
        self.assertEqual(self.collect()["checks"][SECRETS]["status"], "present")

    # ----------------------------------------------------------------- committed environment files

    def test_secret_named_values_in_committed_environment_files_are_violations(self) -> None:
        self.write(
            ".env",
            "NEXT_PUBLIC_APP_URL=https://example.org\n"
            f"SESSION_SECRET={ENV_SECRET}\n"
            "STRIPE_SECRET_KEY=\n"
            "DATABASE_URL=postgres://postgres:postgres@localhost:5432/dev\n"
            "API_KEY=your-api-key-here\n"
            "NEXT_PUBLIC_MAP_TOKEN=pk.public-map-token-1234\n",
        )
        self.write(".env.example", f"SESSION_SECRET={ENV_SECRET}\n")
        self.commit()
        result = self.collect()
        secrets = result["checks"][SECRETS]
        self.assertEqual(secrets["status"], "violation")
        self.assertEqual(secrets["evidence"], [".env:2 — secret-named variable SESSION_SECRET: `SESSION_SECRET=<REDACTED>`"])
        self.assertEqual(result["snapshot"]["environmentFiles"]["committed"], [".env"])
        self.assert_verifies(result)
        self.assert_no_values(result, ENV_SECRET)

    def test_committed_defaults_without_secrets_pass_the_secret_check(self) -> None:
        self.write(".env", "NEXT_PUBLIC_APP_URL=https://example.org\nLOG_LEVEL=info\n")
        self.write(".env.local", "FEATURE_FLAG=on\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["checks"][SECRETS]["status"], "present")
        self.assertEqual(result["snapshot"]["environmentFiles"]["committedLocal"], [".env.local"])

    def test_quoted_and_exported_environment_values_are_redacted(self) -> None:
        self.write("deploy/production.env", f'export DB_PASSWORD="{ENV_SECRET}"  \n')
        self.commit()
        result = self.collect()
        self.assertEqual(result["checks"][SECRETS]["evidence"], ["deploy/production.env:1 — secret-named variable DB_PASSWORD: `DB_PASSWORD=<REDACTED>`"])
        self.assert_verifies(result)
        self.assert_no_values(result, ENV_SECRET)

    # ----------------------------------------------------------------- environment ignore rules

    def test_no_ignore_rule_is_missing(self) -> None:
        self.write(".gitignore", "node_modules\n")
        self.commit()
        result = self.collect()
        ignored = result["checks"][IGNORED]
        self.assertEqual(ignored["status"], "missing")
        self.assertIn(".env.local", ignored["evidence"][0])
        self.assert_verifies(result)

    def test_a_repository_rule_covering_every_local_file_is_present(self) -> None:
        self.write(".gitignore", "node_modules\n.env*\n!.env.example\n")
        self.write(".env.example", "SESSION_SECRET=\n")
        self.commit()
        result = self.collect()
        ignored = result["checks"][IGNORED]
        self.assertEqual(ignored["status"], "present")
        self.assertIn(".gitignore:2 — `.env*` ignores .env", ignored["evidence"])
        self.assert_verifies(result)

    def test_a_negated_or_partial_rule_is_partial(self) -> None:
        self.write(".gitignore", ".env*.local\n!.env.production.local\n")
        self.commit()
        result = self.collect()
        ignored = result["checks"][IGNORED]
        self.assertEqual(ignored["status"], "partial")
        self.assertIn(".env.production.local", ignored["evidence"][0])
        self.assertIn(".env,", ignored["evidence"][0])
        self.assert_verifies(result)

    def test_a_personal_exclude_file_does_not_protect_the_team(self) -> None:
        self.write("src/a.ts", "export const a = 1;\n")
        self.commit()
        exclude = self.root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(".env*\n", encoding="utf-8")
        result = self.collect()
        ignored = result["checks"][IGNORED]
        self.assertEqual(ignored["status"], "missing")
        self.assertIn("personal, or global ignore file", ignored["gap"])
        self.assert_verifies(result)

    def test_a_tracked_env_file_is_not_required_to_be_ignored(self) -> None:
        self.write(".gitignore", ".env*.local\n")
        self.write(".env", "LOG_LEVEL=info\n")
        self.commit()
        self.assertEqual(self.collect()["checks"][IGNORED]["status"], "present")

    def test_nested_applications_need_their_own_coverage(self) -> None:
        self.write(".gitignore", "/.env*\n")
        self.write("apps/web/next.config.mjs", "export default {};\n")
        self.commit()
        ignored = self.collect()["checks"][IGNORED]
        self.assertEqual(ignored["status"], "partial")
        self.assertIn("apps/web/.env.local", ignored["evidence"][0])

    # ----------------------------------------------------------------- dynamic code execution

    def test_eval_and_new_function_in_shipped_code_are_violations(self) -> None:
        self.write(
            "src/run.ts",
            "export function run(code: string) {\n"
            "  const result = eval(code);\n"
            "  const make = new Function('a', code);\n"
            "  setTimeout(`tick()`, 10);\n"
            "  window.eval (code);\n"
            "  return [result, make];\n"
            "}\n",
        )
        self.commit()
        result = self.collect()
        dynamic = result["checks"][DYNAMIC]
        self.assertEqual(dynamic["status"], "violation")
        self.assertEqual(dynamic["judgement"], "consider")
        self.assertEqual(
            dynamic["evidence"],
            ["src/run.ts:2 — `eval(`", "src/run.ts:3 — `new Function(`", "src/run.ts:4 — `setTimeout(`", "src/run.ts:5 — `window.eval (`"],
        )
        self.assert_verifies(result)

    def test_safe_lookalikes_comments_tests_and_minified_vendors_are_not_violations(self) -> None:
        self.write(
            "src/safe.ts",
            "// eval(code) would be unsafe here\n"
            "/* new Function('x') */\n"
            "export const evaluate = (model: { eval(x: string): number }) => model.eval('1');\n"
            "export const retry = () => setTimeout(() => retry(), 1000);\n"
            "export const medieval = (x: number) => x;\n",
        )
        self.write("src/run.test.ts", "it('evaluates', () => expect(eval('1 + 1')).toBe(2));\n")
        self.write("public/vendor/lib.min.js", "!function(){eval('x')}();\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["checks"][DYNAMIC]["status"], "present")
        self.assertIn("1 in tests only", result["checks"][DYNAMIC]["evidence"][0])
        self.assertEqual(result["snapshot"]["dynamicCodeExecution"], {"shippedSites": 0, "testSites": 1})

    # ----------------------------------------------------------------- large-language-model layer

    def test_without_a_model_sdk_the_llm_checks_stay_for_the_model_to_confirm(self) -> None:
        self.write("package.json", '{"dependencies": {"next": "15.3.3", "react": "19.1.0"}}\n')
        self.commit()
        result = self.collect()
        self.assertEqual(set(result["checks"]), {SECRETS, IGNORED, DYNAMIC})
        self.assertIn("Confirm there is no model call", result["snapshot"]["aiDetection"])

    def test_enterprise_and_compatible_model_routes_are_detected(self) -> None:
        cases = {
            "ai sdk": ('{"dependencies": {"@ai-sdk/openai": "2.0.0"}}\n', "export const a = 1;\n"),
            "bedrock": ('{"dependencies": {"@aws-sdk/client-bedrock-runtime": "3.700.0"}}\n', "export const a = 1;\n"),
            "mastra": ('{"dependencies": {"@mastra/core": "0.10.0"}}\n', "export const a = 1;\n"),
            "vertex": ('{"dependencies": {"@google-cloud/vertexai": "1.9.0"}}\n', "export const a = 1;\n"),
            "anthropic host": ('{"dependencies": {}}\n', "export const ask = () => fetch('https://api.anthropic.com/v1/messages');\n"),
            "azure host": ('{"dependencies": {}}\n', "export const ask = () => fetch('https://acme.openai.azure.com/openai/deployments/x');\n"),
            "compatible endpoint": ('{"dependencies": {}}\n', "export const ask = () => fetch(`${base}/v1/chat/completions`);\n"),
        }
        for name, (manifest, source) in cases.items():
            with self.subTest(case=name):
                self.write("package.json", manifest)
                self.write("src/model.ts", source)
                self.commit(name)
                snapshot = self.collect()["snapshot"]
                self.assertTrue(snapshot["aiSdks"] or snapshot["modelApiReferences"]["count"], name)
                self.assertNotIn("aiDetection", snapshot)

    # ----------------------------------------------------------------- snapshot

    def test_snapshot_inventories_entry_points_and_sinks(self) -> None:
        self.write("package.json", '{"dependencies": {"next": "15.1.6", "react": "19.0.0", "zod": "3.23.8", "jose": "5.9.6", "ai": "5.0.0", "isomorphic-dompurify": "2.16.0"}}\n')
        self.write("middleware.ts", "export function middleware() {}\nexport const config = { matcher: ['/dashboard/:path*'] };\n")
        self.write(
            "app/invoices/actions.ts",
            "'use server';\n\nexport async function deleteInvoice(id: string) {}\nexport const archive = async (id: string) => id;\n",
        )
        self.write(
            "app/invoices/page.tsx",
            "export default function Page() {\n  async function save() {\n    'use server';\n  }\n  return <form action={save} />;\n}\n",
        )
        self.write(
            "app/api/invoices/[id]/route.ts",
            "export async function GET() {}\nexport const DELETE = async () => {};\nconst handler = () => {};\nexport { handler as PATCH };\n",
        )
        self.write("pages/api/legacy.ts", "export default function handler() {}\n")
        self.write(
            "app/api/preview/route.ts",
            "export async function GET(request: Request) {\n"
            "  const url = new URL(request.url).searchParams.get('url')!;\n"
            "  await fetch('https://api.example.com/fixed');\n"
            "  return fetch(url);\n}\n",
        )
        self.write(
            "app/login/LoginForm.tsx",
            "'use client';\n"
            "export function LoginForm({ next }: { next: string }) {\n"
            "  localStorage.setItem('accessToken', 'value');\n"
            "  window.location.assign(next);\n"
            "  window.addEventListener('message', () => {});\n"
            "  return <div dangerouslySetInnerHTML={{ __html: next }} />;\n"
            "}\n",
        )
        self.write("src/legacy.ts", "export const paint = (el: Element, html: string) => { el.innerHTML = html; };\n")
        self.write(".env.example", "NEXT_PUBLIC_STRIPE_SECRET_KEY=\nNEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=\n")
        self.write("next.config.ts", "export default { async headers() { return [{ source: '/(.*)', headers: [{ key: 'X-Frame-Options', value: 'DENY' }] }]; } };\n")
        self.write("lib/server.ts", "import 'server-only';\nexport const secret = process.env.SESSION_SECRET;\n")
        self.commit()
        snapshot = self.collect()["snapshot"]
        self.assertEqual(snapshot["frameworks"], {"next": "15.1.6", "react": "19.0.0"})
        self.assertEqual(snapshot["nextRouter"], "app+pages")
        self.assertEqual(
            snapshot["serverActions"]["sites"],
            ["app/invoices/actions.ts:3 deleteInvoice", "app/invoices/actions.ts:4 archive", "app/invoices/page.tsx:3 (inline action)"],
        )
        self.assertEqual(
            snapshot["routeHandlers"]["sites"],
            ["app/api/invoices/[id]/route.ts GET,DELETE,PATCH", "app/api/preview/route.ts GET", "pages/api/legacy.ts (pages API route)"],
        )
        self.assertEqual(snapshot["middleware"], ["middleware.ts"])
        self.assertEqual(snapshot["authenticationLibraries"], ["jose"])
        self.assertEqual(snapshot["validationLibraries"], ["zod"])
        self.assertEqual(snapshot["sanitizers"], ["isomorphic-dompurify"])
        self.assertEqual(snapshot["aiSdks"], ["ai"])
        self.assertEqual(snapshot["requestsWithDynamicUrl"]["sites"], ["app/api/preview/route.ts:4"])
        self.assertEqual(snapshot["dynamicRedirects"]["sites"], ["app/login/LoginForm.tsx:4"])
        self.assertEqual(snapshot["browserStorageAuthKeys"]["sites"], ["app/login/LoginForm.tsx:3 accessToken"])
        self.assertEqual(snapshot["messageListeners"]["sites"], ["app/login/LoginForm.tsx:5"])
        self.assertEqual(snapshot["dangerouslySetInnerHTML"]["sites"], ["app/login/LoginForm.tsx:6"])
        self.assertEqual(snapshot["otherHtmlSinks"]["sites"], ["src/legacy.ts:1"])
        self.assertEqual(snapshot["publicEnvironmentNamesThatLookSecret"], ["NEXT_PUBLIC_STRIPE_SECRET_KEY (.env.example:1)"])
        self.assertEqual(snapshot["serverOnlyImports"]["sites"], ["lib/server.ts"])
        self.assertEqual(snapshot["clientComponentFiles"], 1)
        self.assertEqual(snapshot["securityHeaderSources"], {"X-Frame-Options": ["next.config.ts"]})
        self.assertIn("Content-Security-Policy", snapshot["securityHeadersNotFound"])

    def test_redirect_candidates_skip_literals_and_declarations(self) -> None:
        self.write(
            "src/navigation.ts",
            "export function go(next: string) {\n"
            "  const location = useLocation();\n"
            "  window.location.href = '/home';\n"
            "  window.location.href = next;\n"
            "  location.href = `${next}`;\n"
            "  redirect('/dashboard');\n"
            "  return location;\n"
            "}\n",
        )
        self.commit()
        self.assertEqual(self.collect()["snapshot"]["dynamicRedirects"]["sites"], ["src/navigation.ts:4", "src/navigation.ts:5"])

    def test_proxy_files_and_remix_routes_are_entry_points(self) -> None:
        self.write("src/proxy.ts", "export function proxy() {}\n")
        self.write("app/routes/invoices.tsx", "export async function loader() {}\nexport async function action() {}\n")
        self.commit()
        snapshot = self.collect()["snapshot"]
        self.assertEqual(snapshot["middleware"], ["src/proxy.ts"])
        self.assertEqual(snapshot["routeHandlers"]["sites"], ["app/routes/invoices.tsx:1 loader", "app/routes/invoices.tsx:2 action"])

    # ----------------------------------------------------------------- history scan

    def test_with_scan_without_gitleaks_degrades_the_secret_check(self) -> None:
        self.write("src/a.ts", "export const a = 1;\n")
        self.commit()
        with mock.patch.object(collector.shutil, "which", return_value=None):
            result = self.collect("--with-scan")
        secrets = result["checks"][SECRETS]
        self.assertEqual(secrets["status"], "present")
        self.assertIn("gitleaks is not installed", secrets["degradedReason"])
        self.assertFalse(result["snapshot"]["secretScan"]["historyScanned"])

    def test_history_findings_report_counts_never_values(self) -> None:
        self.write("src/a.ts", "export const a = 1;\n")
        self.commit()
        report = [
            {"RuleID": "stripe-access-token", "File": "lib/old.ts", "Commit": "a" * 40, "Secret": STRIPE, "Match": f"key = '{STRIPE}'"},
            {"RuleID": "generic-api-key", "File": "lib/old.ts", "Commit": "b" * 40, "Secret": ENV_SECRET, "Match": ENV_SECRET},
        ]
        with mock.patch.object(collector, "run_gitleaks", return_value=(report, "gitleaks git --redact --log-opts=--all")):
            result = self.collect("--with-scan")
        secrets = result["checks"][SECRETS]
        self.assertEqual(secrets["status"], "violation")
        self.assertEqual(secrets["judgement"], "act-on")
        self.assertEqual(
            secrets["evidence"],
            ["command: `gitleaks git --redact --log-opts=--all` → 2 finding(s) in 2 commit(s) (generic-api-key ×1, stripe-access-token ×1); values redacted"],
        )
        self.assertEqual(result["snapshot"]["secretScan"]["history"]["commits"], ["a" * 12, "b" * 12])
        self.assert_verifies(result)
        self.assert_no_values(result, STRIPE, ENV_SECRET)

    def test_generic_history_findings_alone_are_considered_not_acted_on(self) -> None:
        self.write("src/a.ts", "export const a = 1;\n")
        self.commit()
        report = [{"RuleID": "generic-api-key", "File": "a.ts", "Commit": "c" * 40}]
        with mock.patch.object(collector, "run_gitleaks", return_value=(report, "gitleaks git --redact --log-opts=--all")):
            secrets = self.collect("with-scan")["checks"][SECRETS]
        self.assertEqual((secrets["status"], secrets["judgement"]), ("violation", "consider"))

    @unittest.skipUnless(shutil.which("gitleaks"), "gitleaks is not installed")
    def test_gitleaks_finds_a_secret_that_was_removed_from_the_tree(self) -> None:
        self.write("lib/payments.ts", f"export const key = '{STRIPE}';\n")
        self.commit("add key")
        self.write("lib/payments.ts", "export const key = process.env.STRIPE_SECRET_KEY;\n")
        self.commit("remove key")
        self.assertEqual(self.collect()["checks"][SECRETS]["status"], "present")
        result = self.collect("--with-scan")
        secrets = result["checks"][SECRETS]
        self.assertEqual(secrets["status"], "violation")
        self.assertTrue(result["snapshot"]["secretScan"]["historyScanned"])
        self.assertNotIn("files", result["snapshot"]["secretScan"]["history"])
        self.assert_verifies(result)
        self.assert_no_values(result, STRIPE)

    # ----------------------------------------------------------------- command line

    def test_the_command_line_prints_the_collector_contract(self) -> None:
        self.write("src/a.ts", f"export const a = '{GITHUB}';\n")
        self.commit()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = collector.main(["--repository", str(self.root), "--threshold=unused=1", "--enrichment=--learn"])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(set(result), {"checks", "snapshot"})
        self.assertEqual(result["checks"][SECRETS]["status"], "violation")
        self.assertNotIn(GITHUB, output.getvalue())

    def test_an_empty_repository_reports_clean_tool_checks(self) -> None:
        self.commit("empty")
        result = self.collect()
        self.assertEqual(result["checks"][SECRETS]["status"], "present")
        self.assertEqual(result["checks"][DYNAMIC]["status"], "present")
        self.assertEqual(result["checks"][IGNORED]["status"], "missing")
        self.assert_verifies(result)

    # ----------------------------------------------------------------- review regressions

    def assert_no_fragments(self, result: dict[str, Any], *values: str, size: int = 8) -> None:
        """No run of `size` characters from any value appears anywhere in the output."""
        text = json.dumps(result)
        for value in values:
            for start in range(len(value) - size + 1):
                self.assertNotIn(value[start:start + size], text, f"fragment of a secret leaked: {value[start:start + size]!r}")

    def test_an_adjacent_secret_on_a_compact_line_never_leaks(self) -> None:
        self.write("src/min.js", f'c={{dbPass:"{GENERIC_PASSWORD}",gh:"{GITHUB}"}};x={{k:"{STRIPE}"}}\n')
        self.write("config/app.json", f'{{"password":"{GENERIC_PASSWORD}","key":"{AWS}","next":"{GENERIC_PASSWORD}"}}\n')
        self.commit()
        result = self.collect()
        self.assertEqual(result["checks"][SECRETS]["status"], "violation")
        self.assert_verifies(result)
        self.assert_no_fragments(result, GENERIC_PASSWORD, GITHUB[4:], STRIPE[8:], AWS[4:])

    def test_a_credential_in_a_file_name_is_withheld(self) -> None:
        self.write(f"keys/{GITHUB}.txt", f"token={GITHUB}\n")
        self.write(f"src/{AWS}.ts", "export const run = (code: string) => eval(code);\n")
        self.commit()
        result = self.collect()
        secrets = result["checks"][SECRETS]
        self.assertEqual(secrets["status"], "violation")
        self.assertTrue(any("paths are withheld" in entry for entry in secrets["evidence"]))
        self.assertTrue(any("withheld because they looked like credentials" in entry for entry in result["checks"][DYNAMIC]["evidence"]))
        self.assert_verifies(result)
        self.assert_no_fragments(result, GITHUB[4:], AWS[4:])

    def test_secrets_in_excluded_folders_and_large_files_are_found(self) -> None:
        self.write("build/credentials.ts", f"export const key = '{STRIPE}';\n")
        self.write("node_modules/vendored/config.js", f"module.exports = '{GITHUB}';\n")
        self.write("data/huge.txt", "x" * (collector.MAX_FILE_BYTES + 10) + f"\nkey={AWS}\n")
        self.commit()
        result = self.collect()
        evidence = " ".join(result["checks"][SECRETS]["evidence"])
        for path in ("build/credentials.ts:1", "node_modules/vendored/config.js:1", "data/huge.txt:2"):
            self.assertIn(path, evidence)
        self.assert_verifies(result)

    def test_placeholders_are_whole_values_not_substrings(self) -> None:
        self.write(
            ".env.production",
            "DB_PASSWORD=CorrectHorseExample42!\n"
            f"PUBLIC_SERVICE_ROLE_KEY={''.join(('Zr8wQe', '2Ty6Ui', '0Op4As'))}\n"
            f"OPENAI_KEY={''.join(('Lk3Jh5', 'Gf7Ds9', 'Aq1Ws2', 'Ed'))}\n"
            f"STRIPE_KEY={''.join(('Rf4Tg6', 'Yh8Uj0', 'Ik2Ol4', 'Pz'))}\n"
            f"SESSION_KEY={''.join(('Mn5Bv7', 'Cx9Zl1', 'Kj3Hg5', 'Fd'))}\n"
            f"GITHUB_PAT={''.join(('Qa2Ws4', 'Ed6Rf8', 'Tg0Yh2', 'Uj'))}\n"
            "API_KEY=your-api-key-here\n"
            "NEXT_PUBLIC_MAP_TOKEN=pk.public-map-token-1234\n"
            "SIGNING_KEY=changeme\n",
        )
        self.commit()
        result = self.collect()
        flagged = [entry.split("variable ")[1].split(":")[0] for entry in result["checks"][SECRETS]["evidence"]]
        self.assertEqual(flagged, ["DB_PASSWORD", "PUBLIC_SERVICE_ROLE_KEY", "OPENAI_KEY", "STRIPE_KEY", "SESSION_KEY", "GITHUB_PAT"])
        self.assert_verifies(result)

    def test_private_key_templates_are_not_keys_but_real_keys_are(self) -> None:
        footer = "-----END RSA " + "PRIVATE KEY-----"
        self.write("docs/keys.md", f"Paste your key:\n\n```\n{PRIVATE_KEY_HEADER}\n...\n{footer}\n```\n")
        self.commit()
        self.assertEqual(self.collect()["checks"][SECRETS]["status"], "present")
        self.write("certs/server.pem", f"{PRIVATE_KEY_HEADER}\n{PRIVATE_KEY_BODY}\n{footer}\n")
        self.write("config/service.json", '{"private_key": "' + PRIVATE_KEY_HEADER + "\\n" + PRIVATE_KEY_BODY + '\\n"}\n')
        self.commit("real keys")
        result = self.collect()
        evidence = result["checks"][SECRETS]["evidence"]
        self.assertEqual([entry.split(" — ")[0] for entry in evidence], ["certs/server.pem:1", "config/service.json:1"])
        self.assert_verifies(result)
        self.assert_no_fragments(result, PRIVATE_KEY_BODY)

    def test_keystore_files_are_secrets_and_test_only_hits_are_considered(self) -> None:
        (self.root / "android").mkdir()
        (self.root / "android" / "release.jks").write_bytes(b"\xfe\xed\xfe\xed\0\0binary")
        self.commit()
        secrets = self.collect()["checks"][SECRETS]
        self.assertEqual((secrets["status"], secrets["judgement"]), ("violation", "act-on"))
        self.assertIn("android/release.jks — committed keystore or private key file", secrets["evidence"])
        self.git("rm", "-q", "android/release.jks")
        self.write("tests/fixtures/payments.ts", f"export const key = '{STRIPE}';\n")
        self.commit("test fixture")
        secrets = self.collect()["checks"][SECRETS]
        self.assertEqual((secrets["status"], secrets["judgement"]), ("violation", "consider"))

    def test_outside_git_the_secret_check_is_not_evaluated(self) -> None:
        plain = Path(self.directory.name) / "plain"
        plain.mkdir()
        (plain / "a.ts").write_text(f"const k = '{STRIPE}';\n", encoding="utf-8")
        result = collector.collect(plain, [])
        self.assertEqual(result["checks"][SECRETS]["evaluationState"], "not-evaluated")
        self.assert_no_fragments(result, STRIPE[8:])

    def test_a_committed_local_environment_file_is_a_violation(self) -> None:
        self.write(".gitignore", ".env*\n")
        self.write("apps/web/next.config.js", "module.exports = {};\n")
        self.write("apps/web/.env.local", "FEATURE_FLAG=on\n")
        self.git("add", "-f", "apps/web/.env.local")
        self.commit()
        result = self.collect()
        ignored = result["checks"][IGNORED]
        self.assertEqual(ignored["status"], "violation")
        self.assertEqual(ignored["evidence"], ["apps/web/.env.local — committed local environment file"])
        self.assertIn("git rm --cached", ignored["remediation"])
        self.assert_verifies(result)

    def test_every_application_folder_and_local_variant_is_probed(self) -> None:
        self.write(".gitignore", "/.env\n/.env.local\n/.env.development.local\n/.env.production.local\n/.env.any-mode.local\n")
        self.write("apps/api/package.json", '{"name": "api", "dependencies": {"express": "5.1.0"}}\n')
        self.commit()
        evidence = self.collect()["checks"][IGNORED]["evidence"][0]
        self.assertIn(".env.test.local", evidence)
        self.assertIn("apps/api/.env.local", evidence)

    def test_an_untracked_ignore_file_protects_no_one(self) -> None:
        self.write("src/a.ts", "export const a = 1;\n")
        self.commit()
        self.write(".gitignore", ".env*\n")
        ignored = self.collect()["checks"][IGNORED]
        self.assertEqual(ignored["status"], "missing")
        self.assertIn("untracked", ignored["gap"])

    def test_eval_in_strings_regexes_and_prose_is_not_code(self) -> None:
        self.write(
            "src/Help.tsx",
            "const message = 'avoid new Function(x) and eval(y)';\n"
            "const pattern = /eval\\(/g;\n"
            "const template = `setTimeout('x') and eval(z)`;\n"
            "export const Help = () => <p>Never call eval() on input</p>;\n"
            "export const Warning = () => <p>\n  eval() is dangerous\n</p>;\n",
        )
        self.commit()
        self.assertEqual(self.collect()["checks"][DYNAMIC]["status"], "present")

    def test_a_call_followed_by_a_block_and_a_ternary_call_are_code(self) -> None:
        self.write("src/run.ts", "export function run(code: string, ok: boolean) {\n  eval(code)\n  {\n    console.log(code);\n  }\n  return ok ? eval(code) : null;\n}\n")
        self.commit()
        dynamic = self.collect()["checks"][DYNAMIC]
        self.assertEqual(dynamic["evidence"], ["src/run.ts:2 — `eval(`", "src/run.ts:6 — `eval(`"])

    def test_pages_and_risky_patterns_are_inventoried(self) -> None:
        self.write("app/invoices/[id]/page.tsx", "export default async function Page({ params }: { params: Promise<{ id: string }> }) { return null; }\n")
        self.write("app/search/page.tsx", "export default async function Page({ searchParams }: { searchParams: Promise<{ q?: string }> }) { return null; }\n")
        self.write("app/about/page.tsx", "export default function Page() { return null; }\n")
        self.write("app/blog/layout.tsx", "export async function generateMetadata() { return {}; }\nexport default function Layout({ children }: { children: React.ReactNode }) { return children; }\n")
        self.write("app/invoices/[id]/Delete.tsx", "'use client';\nexport const Delete = ({ id }: { id: string }) => <form action={remove.bind(null, id)} />;\n")
        self.write("app/api/data/route.ts", "export async function GET() { return new Response('x', { headers: { 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'public, max-age=60' } }); }\n")
        self.write("app/api/webhooks/stripe/route.ts", "export async function POST(request: Request) { stripe.webhooks.constructEvent(await request.text(), request.headers.get('stripe-signature')!, secret); }\n")
        self.write("app/api/upload/route.ts", "export async function POST(request: Request) { const file = (await request.formData()).get('file'); if (file instanceof File) return null; }\n")
        self.write("lib/data.ts", "export const load = unstable_cache(async () => 1, ['k']);\n")
        self.commit()
        snapshot = self.collect()["snapshot"]
        self.assertEqual(
            snapshot["pageEntryPoints"]["sites"],
            ["app/blog/layout.tsx (generateMetadata)", "app/invoices/[id]/page.tsx (dynamic segment)", "app/search/page.tsx (searchParams)"],
        )
        self.assertEqual(snapshot["boundActionArguments"]["sites"], ["app/invoices/[id]/Delete.tsx:2"])
        self.assertEqual(snapshot["corsSites"]["sites"], ["app/api/data/route.ts:1"])
        self.assertEqual(snapshot["webhookSites"]["count"], 2)
        self.assertEqual(snapshot["uploadSites"]["sites"], ["app/api/upload/route.ts:1"])
        self.assertEqual(snapshot["cachingSites"]["sites"], ["app/api/data/route.ts:1", "lib/data.ts:1"])

    def test_storage_keys_that_look_like_credentials_are_not_shown(self) -> None:
        self.write("src/store.ts", f"localStorage.setItem('token_{GENERIC_PASSWORD}', 'x');\nlocalStorage.setItem('authToken', 'x');\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["snapshot"]["browserStorageAuthKeys"]["sites"], ["src/store.ts:1 (key not shown)", "src/store.ts:2 authToken"])
        self.assert_no_fragments(result, GENERIC_PASSWORD)

    def test_catalog_rates_controls_by_the_defect_they_prevent(self) -> None:
        catalog = json.loads((ROOT / "security-audit" / "checks.json").read_text(encoding="utf-8"))
        severities = {check["checkId"]: check["severity"] for check in catalog["checks"]}
        self.assertEqual(severities[CHECK + "env-files-gitignored"], "critical")
        self.assertEqual(severities[CHECK + "no-secrets-in-prompts"], "critical")
        self.assertEqual(severities[CHECK + "no-secrets-in-source"], "critical")

    def test_leak_graders_reject_every_fragment_of_the_fixture_key(self) -> None:
        case = ROOT / "evals" / "security-audit" / "vulnerable-app"
        assignment = next(line for line in (case / "fixture.sh").read_text(encoding="utf-8").splitlines() if line.startswith("stripe_key="))
        key = "".join(re.findall(r'"([^"]*)"', assignment))
        body = key[len("sk_live_"):]
        graders = sorted((case / "graders").glob("key-not-in-*.md"))
        targets = {re.search(r"path: (\S+) \}", grader.read_text(encoding="utf-8")).group(1) for grader in graders}
        self.assertEqual(
            targets,
            {".architect-audits/security-audit/" + name for name in ("findings.md", "findings.json", "snapshot.md")},
        )
        clean = "## All checks\nnothing to see <REDACTED>\n"
        for grader in graders:
            text = grader.read_text(encoding="utf-8")
            self.assertNotIn("---", text.split("---\n")[1].split("pattern:")[1])
            pattern = json.loads(re.search(r'^pattern: (".*")$', text, re.M).group(1))
            with self.subTest(grader=grader.name):
                self.assertIsNotNone(re.search(pattern, clean))
                for start in range(len(body) - 7):
                    self.assertIsNone(re.search(pattern, clean + body[start:start + 8]), body[start:start + 8])
    # ----------------------------------------------------------------- second review regressions

    def test_a_credential_shaped_file_or_folder_name_is_withheld_everywhere(self) -> None:
        self.write(f"src/{LONG_PASSWORD}.ts", "export const run = (input: string) => eval(input);\n")
        self.write(f"lib/{LONG_PASSWORD}/view.tsx", "export const View = ({ html }: { html: string }) => <div dangerouslySetInnerHTML={{ __html: html }} />;\n")
        self.write(f"apps/{LONG_PASSWORD}/package.json", '{"name": "hidden"}\n')
        self.commit()
        result = self.collect()
        self.assertFalse(collector.path_is_safe(f"lib/{LONG_PASSWORD}/view.tsx"))
        self.assertTrue(any("withheld" in entry for entry in result["checks"][DYNAMIC]["evidence"]))
        self.assert_verifies(result)
        self.assert_no_fragments(result, LONG_PASSWORD)

    def test_ordinary_names_are_not_mistaken_for_credentials(self) -> None:
        for name in ("useOAuth2Callback1Handler", "UserProfile2FactorSettings", "20240101120000_create_invoices", "NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY"):
            self.assertFalse(collector.credential_like(name), name)
        self.assertTrue(collector.credential_like(LONG_PASSWORD))

    def test_discovered_and_unlisted_local_variants_are_probed(self) -> None:
        listed = (".env", ".env.local", ".env.development.local", ".env.test.local", ".env.production.local", ".env.staging.local")
        self.write(".gitignore", "".join(f"/{name}\n" for name in listed))
        self.commit()
        self.write(".env.qa.local", "FEATURE=on\n")
        ignored = self.collect()["checks"][IGNORED]
        self.assertEqual(ignored["status"], "partial")
        self.assertIn(".env.qa.local", ignored["evidence"][0])
        self.assertIn(".env.any-mode.local", ignored["evidence"][0])

    def test_eval_in_template_interpolations_and_after_comparisons_is_code(self) -> None:
        self.write(
            "src/run.ts",
            "export function run(input: string) {\n"
            "  const label = `value: ${eval(input)}`;\n"
            "  const bigger = 1 > eval(input);\n"
            "  const nested = `a ${`b ${new Function(input)}`}`;\n"
            "  return [label, bigger, nested];\n"
            "}\n",
        )
        self.commit()
        dynamic = self.collect()["checks"][DYNAMIC]
        self.assertEqual(dynamic["evidence"], ["src/run.ts:2 — `eval(`", "src/run.ts:3 — `eval(`", "src/run.ts:4 — `new Function(`"])

    def test_environment_values_are_parsed_before_placeholder_and_host_rules(self) -> None:
        self.write(
            ".env.production",
            f"DB_PASSWORD={GENERIC_PASSWORD[:6]}localhost{GENERIC_PASSWORD[6:]}\n"
            'PASSWORD="changeme" # fill later\n'
            "API_SECRET='your-secret-here'  # from the dashboard\n"
            "DATABASE_URL=postgres://app:" + GENERIC_PASSWORD + "@localhost:5432/app\n"
            f'SESSION_SECRET="{GENERIC_PASSWORD[:8]}#{GENERIC_PASSWORD[8:]}" # rotated monthly\n'
            f"SIGNING_KEY={GENERIC_PASSWORD} # set by the platform\n",
        )
        self.commit()
        result = self.collect()
        flagged = [entry.split("variable ")[1].split(":")[0] for entry in result["checks"][SECRETS]["evidence"]]
        self.assertEqual(flagged, ["DB_PASSWORD", "SESSION_SECRET", "SIGNING_KEY"])
        self.assert_verifies(result)
        self.assert_no_fragments(result, GENERIC_PASSWORD)
    # ----------------------------------------------------------------- third review regressions

    def test_a_detected_value_is_redacted_wherever_it_reappears(self) -> None:
        for value in (HEX_PASSWORD, WORD_PASSWORD):
            with self.subTest(value=value[:4]):
                self.write(".env", f"DB_PASSWORD={value}\n")
                self.write(f"src/{value}.ts", "export const run = (input: string) => eval(input);\n")
                self.write(f"lib/{value}/page.tsx", "export const html = (x: string) => <div dangerouslySetInnerHTML={{ __html: x }} />;\n")
                self.commit(value[:4])
                result = self.collect()
                self.assertEqual(result["checks"][SECRETS]["status"], "violation")
                self.assertTrue(any("withheld" in entry for entry in result["checks"][DYNAMIC]["evidence"]))
                self.assert_verifies(result)
                self.assert_no_fragments(result, value)
                self.git("rm", "-q", "-r", "--cached", ".")
                for path in list(self.root.iterdir()):
                    if path.name != ".git":
                        shutil.rmtree(path) if path.is_dir() else path.unlink()
        self.assertFalse(collector.credential_like(WORD_PASSWORD))
        self.assertTrue(collector.credential_like(HEX_PASSWORD))

    def test_braces_in_comments_and_regexes_do_not_end_an_interpolation(self) -> None:
        self.write(
            "src/run.ts",
            "export function run(input: string) {\n"
            "  const a = `${1 /* } */ + eval(input)}`;\n"
            "  const b = `${ /}/.test(input) ? eval(input) : 0 }`;\n"
            "  return [a, b];\n"
            "}\n",
        )
        self.commit()
        self.assertEqual(self.collect()["checks"][DYNAMIC]["evidence"], ["src/run.ts:2 — `eval(`", "src/run.ts:3 — `eval(`"])

    def test_a_committed_local_file_with_an_unprintable_path_is_still_a_violation(self) -> None:
        self.write(".gitignore", ".env*\n")
        self.write(f"apps/{LONG_PASSWORD}/.env.local", "FEATURE=on\n")
        self.git("add", "-f", f"apps/{LONG_PASSWORD}/.env.local")
        self.commit()
        result = self.collect()
        ignored = result["checks"][IGNORED]
        self.assertEqual(ignored["status"], "violation")
        self.assertIn("paths are withheld", ignored["evidence"][-1])
        self.assert_verifies(result)
        self.assert_no_fragments(result, LONG_PASSWORD)

    def test_punctuation_in_a_password_does_not_make_it_a_placeholder(self) -> None:
        self.write(
            "config/database.ts",
            f"export const primary = 'postgres://app:{PUNCTUATED_PASSWORD}@prod.internal/app';\n"
            "export const templated = 'postgres://app:${DB_PASSWORD}@prod.internal/app';\n"
            "export const shell = 'postgres://app:$DB_PASSWORD@prod.internal/app';\n"
            "export const documented = 'postgres://app:<password>@prod.internal/app';\n"
            "export const masked = 'postgres://app:********@prod.internal/app';\n",
        )
        self.commit()
        result = self.collect()
        evidence = result["checks"][SECRETS]["evidence"]
        self.assertEqual([entry.split(" — ")[0] for entry in evidence], ["config/database.ts:1"])
        self.assert_verifies(result)
        self.assert_no_fragments(result, PUNCTUATED_PASSWORD)

    def test_a_negation_that_can_match_a_local_env_file_is_the_finding(self) -> None:
        cases = {
            "!.env.staging.local": "partial",
            "!.env.qa.local": "partial",
            "!/apps/web/.env.*.local": "partial",
            "!.env.[pq]a.local": "partial",
            "!**/.env.local": "partial",
            "!*.local": "partial",
            "!.env.example": "present",
            "!docs/*.md": "present",
        }
        for negation, expected in cases.items():
            with self.subTest(negation=negation):
                self.write(".gitignore", f".env\n.env*.local\n{negation}\n")
                self.commit(negation)
                result = self.collect()
                ignored = result["checks"][IGNORED]
                self.assertEqual(ignored["status"], expected)
                if expected == "partial":
                    self.assertIn(f".gitignore:3 — `{negation}` un-ignores a local environment file", ignored["evidence"])
                self.assert_verifies(result)
    # ----------------------------------------------------------------- fourth review regressions

    def test_short_detected_values_are_redacted_whole(self) -> None:
        self.write(".env", f"DB_PASSWORD={SHORT_PASSWORD}\n")
        self.write(f"src/{SHORT_PASSWORD}.ts", "export const run = (input: string) => eval(input);\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["checks"][SECRETS]["status"], "violation")
        self.assertNotIn(SHORT_PASSWORD, json.dumps(result))
        self.assert_verifies(result)
        redaction = collector.Redaction(["q7z"])
        self.assertTrue(redaction.matches("src/q7z.ts"))
        self.assertTrue(redaction.matches("q7z (inline action)"))
        self.assertFalse(redaction.matches("src/aq7zb.ts"))

    def test_private_key_bodies_are_redacted_wherever_they_reappear(self) -> None:
        header = PRIVATE_KEY_HEADER
        footer = "-----END RSA " + "PRIVATE KEY-----"
        self.write("certs/server.pem", f"{header}\n{PLAIN_KEY_BODY}\n{footer}\n")
        self.write("config/service.json", '{"private_key": "' + header + "\\n" + PLAIN_KEY_BODY[::-1] + "\\n" + footer + '"}\n')
        self.write(f"src/{PLAIN_KEY_BODY[:14]}.ts", "export const run = (input: string) => eval(input);\n")
        self.write(f"lib/{PLAIN_KEY_BODY[::-1][:14]}/page.tsx", "export const html = (x: string) => <div dangerouslySetInnerHTML={{ __html: x }} />;\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["checks"][SECRETS]["status"], "violation")
        self.assert_verifies(result)
        self.assert_no_fragments(result, PLAIN_KEY_BODY, PLAIN_KEY_BODY[::-1])

    def test_history_never_prints_file_names(self) -> None:
        self.write("src/a.ts", "export const a = 1;\n")
        self.commit()
        report = [{"RuleID": "generic-api-key", "File": f"keys/{WORD_PASSWORD}.txt", "Commit": "c" * 40}]
        with mock.patch.object(collector, "run_gitleaks", return_value=(report, "gitleaks git --redact --log-opts=--all")):
            result = self.collect("--with-scan")
        self.assertEqual(result["snapshot"]["secretScan"]["history"], {"findings": 1, "rules": {"generic-api-key": 1}, "commits": ["c" * 12]})
        self.assertNotIn(WORD_PASSWORD, json.dumps(result))

    def test_single_quoted_values_are_literal_and_only_references_are_exempt(self) -> None:
        self.write(
            ".env.production",
            f"DB_PASSWORD='{DOLLAR_PASSWORD}'\n"
            f'API_SECRET="{DOLLAR_PASSWORD}"\n'
            "SESSION_SECRET=${BASE_SECRET}\n"
            "SIGNING_SECRET=$BASE_SECRET\n"
            'TOKEN_SECRET="${BASE_SECRET}"\n',
        )
        self.commit()
        result = self.collect()
        flagged = [entry.split("variable ")[1].split(":")[0] for entry in result["checks"][SECRETS]["evidence"]]
        self.assertEqual(flagged, ["DB_PASSWORD", "API_SECRET"])
        self.assert_verifies(result)
        self.assert_no_fragments(result, DOLLAR_PASSWORD)

    def test_the_redaction_guarantee_is_documented(self) -> None:
        notes = (ROOT / "security-audit" / "references" / "detection.md").read_text(encoding="utf-8")
        self.assertIn("## Redaction guarantee", notes)
        self.assertIn("Redaction guarantee", collector.__doc__ or "")
    # ----------------------------------------------------------------- fifth review regressions

    def test_values_equal_to_contract_words_never_corrupt_the_collector_contract(self) -> None:
        self.write(
            ".env.production",
            "PASSWORD=direct\n"
            "API_SECRET=present\n"
            "SESSION_SECRET=violation\n"
            "SIGNING_SECRET=security-audit.no-dynamic-code-execution\n"
            "TOKEN_SECRET=evidenceTier\n",
        )
        self.write("src/run.ts", "export const run = (input: string) => eval(input);\n")
        self.commit()
        result = self.collect()
        for check_id, outcome in result["checks"].items():
            self.assertTrue(check_id.startswith(CHECK))
            self.assertIn(outcome["status"], ("present", "partial", "missing", "violation"))
            self.assertIn(outcome["evidenceTier"], ("direct", "supported", "inferred"))
        self.assertEqual(result["checks"][SECRETS]["status"], "violation")
        self.assertIn("secretScan", result["snapshot"])
        completed = subprocess.run(
            [sys.executable, str(ROOT / "audit-protocol" / "scripts" / "audit_run.py"), "--repository", str(self.root), "begin", "security-audit"],
            capture_output=True, text=True, check=True,
        )
        self.assertIn("Collector resolved 3 checks.", completed.stdout)
        self.assertNotIn("rejected", completed.stdout)
        self.assertNotIn("Warning", completed.stdout)
        run = json.loads((self.root / ".architect-audits" / "security-audit" / ".staging" / "run.json").read_text(encoding="utf-8"))
        recorded = {check["checkId"]: check for check in run["checks"] if check["recordedBy"] == "collector"}
        self.assertEqual(set(recorded), {SECRETS, IGNORED, DYNAMIC})
        self.assertEqual(recorded[SECRETS]["evidenceTier"], "direct")

    def test_a_later_pattern_that_re_ignores_an_exception_wins(self) -> None:
        orderings = {
            ".env*\n!.env.staging.local\n.env*.local\n": "present",
            ".env*\n.env*.local\n!.env.staging.local\n": "partial",
        }
        for content, expected in orderings.items():
            with self.subTest(content=content):
                self.write(".gitignore", content)
                self.commit(expected)
                self.assertEqual(self.collect()["checks"][IGNORED]["status"], expected)

    def test_a_deeper_ignore_file_that_re_ignores_an_exception_wins(self) -> None:
        self.write(".gitignore", ".env*\n!apps/web/.env.local\n")
        self.write("apps/web/.gitignore", ".env.local\n")
        self.write("apps/web/package.json", '{"name": "web"}\n')
        self.commit()
        self.assertEqual(self.collect()["checks"][IGNORED]["status"], "present")

    def test_skipped_source_files_degrade_a_clean_dynamic_code_result(self) -> None:
        self.write("src/at-limit.ts", "x" * collector.MAX_FILE_BYTES)
        self.commit()
        dynamic = self.collect()["checks"][DYNAMIC]
        self.assertEqual(dynamic["status"], "present")
        self.assertNotIn("degradedReason", dynamic)
        self.write("src/over-limit.ts", "x" * (collector.MAX_FILE_BYTES + 1))
        self.commit("over")
        result = self.collect()
        self.assertEqual(result["checks"][DYNAMIC]["status"], "present")
        self.assertIn("1 source files were too large or could not be read", result["checks"][DYNAMIC]["degradedReason"])
        self.assertEqual(result["snapshot"]["skippedSourceFiles"]["examples"], ["src/over-limit.ts"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can read any file")
    def test_an_unreadable_source_file_degrades_a_clean_dynamic_code_result(self) -> None:
        self.write("src/locked.ts", "export const run = (input: string) => eval(input);\n")
        self.commit()
        locked = self.root / "src" / "locked.ts"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o644)
        dynamic = self.collect()["checks"][DYNAMIC]
        self.assertEqual(dynamic["status"], "present")
        self.assertIn("could not be read", dynamic["degradedReason"])

    def test_a_regex_after_a_control_condition_is_not_code(self) -> None:
        self.write(
            "src/run.ts",
            "export function run(ok: boolean, x: string, a: number, b: number, c: number, input: string) {\n"
            "  if (ok) /eval(input)/.test(x);\n"
            "  while (ok) /new Function(/.exec(x);\n"
            "  const ratio = (a + b) / c + eval(input) / 2;\n"
            "  return ratio;\n"
            "}\n",
        )
        self.commit()
        self.assertEqual(self.collect()["checks"][DYNAMIC]["evidence"], ["src/run.ts:4 — `eval(`"])
    # ----------------------------------------------------------------- sixth review regressions

    def test_multiline_quoted_environment_values_are_found(self) -> None:
        middle = "".join(("Correct", "Horse", "Battery", "Staple42!"))
        self.write(
            ".env.production",
            f'SESSION_SECRET="\n{middle}\n"\n'
            "LOG_LEVEL=info\n"
            "API_SECRET='\nchangeme\n'\n"
            f"SIGNING_SECRET={GENERIC_PASSWORD}\n",
        )
        self.commit()
        result = self.collect()
        evidence = result["checks"][SECRETS]["evidence"]
        self.assertEqual(
            evidence,
            [
                ".env.production:1 — secret-named variable SESSION_SECRET: `SESSION_SECRET=<REDACTED>`",
                ".env.production:8 — secret-named variable SIGNING_SECRET: `SIGNING_SECRET=<REDACTED>`",
            ],
        )
        self.assert_verifies(result)
        self.assert_no_fragments(result, middle, GENERIC_PASSWORD)

    def test_function_constructor_calls_are_found_with_or_without_new(self) -> None:
        self.write(
            "src/run.ts",
            "export function run(request: { body: { code: string } }, cb: Function) {\n"
            "  const direct = Function(request.body.code);\n"
            "  const built = new Function(request.body.code);\n"
            "  const helper = registry.Function(request.body.code);\n"
            "  return [direct, built, helper, cb];\n"
            "}\n"
            "function Function(code: string) { return code; }\n"
            "interface Factory { Function(): void }\n",
        )
        self.commit()
        evidence = self.collect()["checks"][DYNAMIC]["evidence"]
        self.assertEqual(evidence, ["src/run.ts:2 — `Function(`", "src/run.ts:3 — `new Function(`"])

    def test_only_exact_example_domains_are_exempt(self) -> None:
        self.write(
            "config/database.ts",
            f"export const a = 'postgres://app:{GENERIC_PASSWORD}@db.notexample.com/app';\n"
            f"export const b = 'postgres://app:{GENERIC_PASSWORD}@myexample.org/app';\n"
            f"export const c = 'postgres://app:{GENERIC_PASSWORD}@api.nottest/app';\n"
            f"export const d = 'postgres://app:{GENERIC_PASSWORD}@db.example.com/app';\n"
            f"export const e = 'postgres://app:{GENERIC_PASSWORD}@example.org/app';\n"
            f"export const f = 'postgres://app:{GENERIC_PASSWORD}@api.staging.test/app';\n",
        )
        self.commit()
        evidence = self.collect()["checks"][SECRETS]["evidence"]
        self.assertEqual([entry.split(" — ")[0] for entry in evidence], ["config/database.ts:1", "config/database.ts:2", "config/database.ts:3"])

    def test_low_diversity_values_are_secrets_unless_one_character_repeats(self) -> None:
        self.write(".env.production", "DB_PASSWORD=abababababab\nAPI_SECRET=xxxxxxxxxxxx\nSIGNING_KEY=000000000000\n")
        self.commit()
        result = self.collect()
        flagged = [entry.split("variable ")[1].split(":")[0] for entry in result["checks"][SECRETS]["evidence"]]
        self.assertEqual(flagged, ["DB_PASSWORD"])
        self.assertNotIn("abababab", json.dumps(result))

if __name__ == "__main__":
    unittest.main()
