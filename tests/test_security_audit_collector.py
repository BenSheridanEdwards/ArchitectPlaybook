from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
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
        self.assertEqual(secrets["evidence"], ["lib/payments.ts:3 — stripe-live-secret-key: `new Stripe('sk_live_<REDACTED>',`"])
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
            "private-key": PRIVATE_KEY_HEADER,
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
        self.assertEqual(evidence[0].count("<REDACTED>"), 2)
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
        self.assertIn("in 1 tracked files", result["checks"][SECRETS]["evidence"][0])

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
        self.assertEqual(result["snapshot"]["environmentFiles"]["committedEnvironmentFiles"], [".env"])
        self.assert_verifies(result)
        self.assert_no_values(result, ENV_SECRET)

    def test_committed_defaults_without_secrets_pass_but_local_files_are_noted(self) -> None:
        self.write(".env", "NEXT_PUBLIC_APP_URL=https://example.org\nLOG_LEVEL=info\n")
        self.write(".env.local", "FEATURE_FLAG=on\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["checks"][SECRETS]["status"], "present")
        self.assertEqual(result["snapshot"]["environmentFiles"]["committedLocalEnvironmentFiles"], [".env.local"])

    def test_quoted_and_exported_environment_values_are_redacted(self) -> None:
        self.write("deploy/production.env", f'export DB_PASSWORD="{ENV_SECRET}"  \n')
        self.commit()
        result = self.collect()
        self.assertEqual(result["checks"][SECRETS]["evidence"], ["deploy/production.env:1 — secret-named variable DB_PASSWORD: `export DB_PASSWORD=<REDACTED>`"])
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
        self.assertEqual(ignored["status"], "partial")
        self.assertIn("personal or global ignore file", ignored["gap"])
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

    def test_llm_checks_are_not_applicable_without_a_model_sdk(self) -> None:
        self.write("package.json", '{"dependencies": {"next": "15.3.3", "react": "19.1.0"}}\n')
        self.commit()
        checks = self.collect()["checks"]
        for name in collector.LLM_CHECKS:
            self.assertEqual(checks[CHECK + name]["applicability"], "not-applicable")

    def test_an_sdk_or_a_direct_api_call_makes_the_llm_checks_applicable(self) -> None:
        cases = {
            "sdk": ('{"dependencies": {"@ai-sdk/openai": "2.0.0"}}\n', "export const a = 1;\n"),
            "direct call": ('{"dependencies": {}}\n', "export const ask = () => fetch('https://api.anthropic.com/v1/messages');\n"),
        }
        for name, (manifest, source) in cases.items():
            with self.subTest(case=name):
                self.write("package.json", manifest)
                self.write("src/model.ts", source)
                self.commit(name)
                result = self.collect()
                for check in collector.LLM_CHECKS:
                    self.assertNotIn(CHECK + check, result["checks"])

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
        self.assertEqual(result["snapshot"]["secretScan"]["history"]["files"], ["lib/payments.ts"])
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


if __name__ == "__main__":
    unittest.main()
