"""Execute the documented skill lookup against isolated installation layouts."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
IDENTITY = "jbaruch/coding-policy"
OWN_ROOT = ".tessl/plugins/" + IDENTITY + "/skills/"
ACCEPTED_ORIGINS = (
    "git@github.com:jbaruch/coding-policy",
    "git@github.com:jbaruch/coding-policy.git",
    "https://github.com/jbaruch/coding-policy",
    "https://github.com/jbaruch/coding-policy.git",
    "ssh://git@github.com/jbaruch/coding-policy",
    "ssh://git@github.com/jbaruch/coding-policy.git",
)


def invocations():
    result = []
    for file in sorted((ROOT / "skills").rglob("*.md")):
        if "tests" in file.relative_to(ROOT).parts:
            continue
        for block in re.findall(r"```bash\n(.*?)```", file.read_text(), re.S):
            lines = block.strip().splitlines()
            if not lines or not lines[0].lstrip().startswith("KS="):
                continue
            bootstrap = lines[0].strip()
            target = re.match(r"KS=\.tessl/plugins/jbaruch/coding-policy/skills/([a-z-]+);", bootstrap)
            if target is None:
                raise AssertionError("unsupported bootstrap in " + str(file))
            for line in lines[1:]:
                match = re.match(r'\s*(bash|python3) "\$KS/([^"\n]+)"', line)
                if match is not None:
                    result.append((file, target.group(1), bootstrap, match.group(1), match.group(2)))
    return result


class SkillBootstrapTest(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.base = Path(holder.name)
        self.project = self.base / "project with spaces"
        self.project.mkdir()
        self.fixture_home = self.base / "global with spaces"
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)

    def representative(self, kind):
        for item in invocations():
            if item[1] == kind:
                return item
        raise AssertionError("no invocation for " + kind)

    def stub(self, root, relative, interpreter, marker):
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        text = "print(" + repr(marker) + ")\n" if interpreter == "python3" else "printf '%s\\n' '" + marker + "'\n"
        file.write_text(text)
        file.chmod(0o644)

    def execute(self, item, bootstrap=None):
        _, _, original, interpreter, relative = item
        # Keep the process HOME intact and substitute only the documented lookup input.
        resolver = (bootstrap or original).replace("$HOME", "$CODING_FIXTURE_HOME")
        code = 'set -euo pipefail\n' + resolver + '\n' + interpreter + ' "$KS/' + relative + '"\n'
        return subprocess.run(["bash", "-c", code], cwd=self.project,
                              env={**os.environ, "CODING_FIXTURE_HOME": str(self.fixture_home)},
                              capture_output=True, text=True)

    def test_every_documented_script_is_bundled_and_has_its_interpreter(self):
        calls = invocations()
        self.assertGreater(len(calls), 20)
        bootstraps = {}
        for file, kind, bootstrap, interpreter, relative in calls:
            with self.subTest(file=str(file), script=relative):
                root = ROOT / "skills" / kind
                script = (root / relative).resolve()
                self.assertTrue(script.is_relative_to(root.resolve()))
                self.assertTrue(script.is_file(), str(script))
                self.assertEqual(interpreter, {".sh": "bash", ".py": "python3"}[script.suffix])
                if kind in bootstraps:
                    self.assertEqual(bootstrap, bootstraps[kind])
                bootstraps[kind] = bootstrap
        self.assertEqual(set(bootstraps), {"release", "adopt-fork-pr", "onboard-repo", "migrate-to-plugin"})

    def test_project_local_lookup_precedes_global(self):
        for kind in ("release", "adopt-fork-pr", "onboard-repo", "migrate-to-plugin"):
            item = self.representative(kind)
            self.stub(self.project / OWN_ROOT / kind, item[4], item[3], "local")
            self.stub(self.fixture_home / OWN_ROOT / kind, item[4], item[3], "global")
            result = self.execute(item)
            self.assertEqual((result.returncode, result.stdout), (0, "local\n"), result.stderr)

    def test_global_lookup_works_with_nonexecutable_files(self):
        for kind in ("release", "adopt-fork-pr", "onboard-repo", "migrate-to-plugin"):
            item = self.representative(kind)
            self.stub(self.fixture_home / OWN_ROOT / kind, item[4], item[3], "global")
            result = self.execute(item)
            self.assertEqual((result.returncode, result.stdout), (0, "global\n"), result.stderr)

    def test_source_clone_requires_one_of_the_exact_own_origins(self):
        for kind in ("release", "adopt-fork-pr", "onboard-repo", "migrate-to-plugin"):
            item = self.representative(kind)
            self.stub(self.project / "skills" / kind, item[4], item[3], "source")
            for origin in ACCEPTED_ORIGINS:
                with self.subTest(kind=kind, origin=origin):
                    subprocess.run(["git", "config", "remote.origin.url", origin], cwd=self.project, check=True)
                    result = self.execute(item)
                    self.assertEqual((result.returncode, result.stdout), (0, "source\n"), result.stderr)

    def test_a_consumer_copy_cannot_impersonate_the_source_clone(self):
        item = self.representative("release")
        self.stub(self.project / "skills/release", item[4], item[3], "impostor")
        for origin in ("https://github.com/other/coding-policy.git",
                       "https://github.com/jbaruch/coding-policy-extra.git",
                       "https://github.com/jbaruch/coding-policy.git/extra"):
            with self.subTest(origin=origin):
                subprocess.run(["git", "config", "remote.origin.url", origin], cwd=self.project, check=True)
                result = self.execute(item)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("impostor", result.stdout)
                self.assertIn("install jbaruch/coding-policy", result.stderr)

    def test_missing_installation_refuses_without_running_a_consumer_script(self):
        result = self.execute(self.representative("release"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("install jbaruch/coding-policy", result.stderr)

    def test_recorded_native_reference_forms_work_without_a_tessl_tree(self):
        for host in (".codex/skills", ".claude/skills", ".cursor/skills", ".agents/skills"):
            for kind in ("release", "adopt-fork-pr", "onboard-repo", "migrate-to-plugin"):
                with self.subTest(host=host, kind=kind):
                    item = self.representative(kind)
                    native = host + "/acr__jbaruch__coding-policy__" + kind
                    bootstrap = item[2].replace(OWN_ROOT + kind, native)
                    bootstrap = bootstrap.replace("KS=skills/" + kind, "KS=" + native)
                    self.stub(self.project / native, item[4], item[3], "native")
                    result = self.execute(item, bootstrap)
                    self.assertEqual((result.returncode, result.stdout), (0, "native\n"), result.stderr)


if __name__ == "__main__":
    unittest.main()
