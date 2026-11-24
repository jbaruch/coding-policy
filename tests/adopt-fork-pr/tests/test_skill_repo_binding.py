"""Exercise the documented fork consent, shell handoffs and origin-bound commands."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SKILL = (Path(__file__).resolve().parents[3] / "skills/adopt-fork-pr") / "SKILL.md"

#: A `gh pr <subcommand>` command in inline code — an instruction the agent runs.
COMMAND = re.compile(r"`(gh pr [^`]*)`")


def step(text, number):
    """The body of `## Step <number> — ...`, up to the next H2."""
    match = re.search(r"^## Step {} —.*?(?=^## |\Z)".format(number), text, re.S | re.M)
    if match is None:
        raise AssertionError("SKILL.md has no Step {}".format(number))
    return match.group(0)


class SkillRepoBindingTest(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")

    def test_every_documented_gh_pr_command_names_origin(self):
        for command in COMMAND.findall(self.text):
            self.assertIn("--repo 'github.com/VALIDATED_REPO'", command)

    def test_adoption_question_previews_both_actions_for_origin(self):
        body = step(self.text, 3)
        before, template = body.split("```text\n", 1)
        prompt, _ = template.split("\n```", 1)
        self.assertIn("plain-text question field", before)
        self.assertIn("PR github.com/<repo>#<number> comes from fork <fork>", prompt)
        self.assertIn("branch <headRefName> at <headRefOid>", prompt)
        self.assertIn("policy reviewer's fork-guard skips fork PRs", prompt)
        self.assertIn("If needed, push this commit to github.com/<repo>:<branch>", prompt)
        self.assertIn("open a same-repo PR into github.com/<repo>:<baseRefName>", prompt)
        self.assertIn("post the missing pointer comment on github.com/<repo>#<number>", prompt)
        self.assertIn(
            "Adopted PR title\n{title}\n\nAdopted PR body\n{body}\n\n"
            "Pointer comment on the original PR\n{pointer}", prompt
        )
        self.assertIn("existing adopted PR on recovery", prompt)
        self.assertIn("URL returned by PR creation", prompt)
        self.assertIn("literal `<new_pr_url>`", before)
        self.assertEqual(self.text.count("`AskUserQuestion`"), 1)
        self.assertIn("only on an explicit **Adopt for review** selection", body)

    def test_short_dialog_defaults_to_inspection(self):
        instructions, template = step(self.text, 3).split("```text\n", 1)
        prompt, choices = template.split("\n```", 1)
        self.assertIn("truncated or inaccessible, report why and offer only **Just inspect**", instructions)
        self.assertIn("Page Up and Page Down", prompt.splitlines()[-1])
        self.assertEqual(re.findall(r"^- \*\*(.*?)\*\* —", choices, re.M), ["Just inspect", "Adopt for review"])


class DocumentedCommandsTest(unittest.TestCase):
    shell = ["bash", "--noprofile", "--norc"]
    repo = "owner/blog-writer"
    origin = "github.com/" + repo
    number = "6"
    oid = "1" * 40
    branch = "adopt/pr-6-feat-example"
    status = ["gh", "pr", "view", number, "--repo", origin, "--json", "reviewDecision,statusCheckRollup"]
    diff = ["gh", "pr", "diff", number, "--repo", origin]
    view = ["gh", "pr", "view", number, "--repo", origin, "--json",
            "number,isCrossRepository,headRefName,headRefOid,headRepositoryOwner,headRepository,author,title,url,state,baseRefName"]
    heads = ["git", "ls-remote", "--heads", "origin", "refs/heads/" + branch]
    preflight = [
        ["git", "rev-parse", "--is-inside-work-tree"],
        ["git", "-C", ".", "remote", "get-url", "origin"],
        ["git", "-C", ".", "remote", "get-url", "--push", "--all", "origin"],
    ]

    def setUp(self):
        self.skill = SKILL.read_text()
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        mount = self.root / ".tessl/plugins/jbaruch/coding-policy/skills"
        shutil.copytree(SKILL.parent, mount / "adopt-fork-pr")
        (mount / "release").mkdir()
        shutil.copyfile(SKILL.parents[1] / "release/origin-repo.py", mount / "release/origin-repo.py")
        self.env = {"PATH": str(self.root) + os.pathsep + os.environ["PATH"], "HOME": str(self.root),
                    "GH_HOST": "enterprise.example.invalid", "GH_REPO": "different/repo"}
        # Only declared argv can succeed. Even an unexpected write stays local.
        stub = """#!/usr/bin/env python3
import json
from pathlib import Path
import sys
argv = [Path(sys.argv[0]).name, *sys.argv[1:]]
with Path("calls").open("a") as log:
    print(json.dumps(argv), file=log)
answer = json.loads(Path("answers").read_text())[json.dumps(argv)]
sys.stdout.buffer.write(answer.encode("utf-8", "surrogateescape"))
"""
        for name in ("git", "gh"):
            path = self.root / name
            path.write_text(stub)
            path.chmod(0o755)
        self.answers = {}
        for cmd, reply in zip(self.preflight, ["true", "https://" + self.origin, "https://" + self.origin]):
            self.answer(cmd, reply)
        self.answer(self.heads, "")
        self.metadata = dict(number=6, isCrossRepository=True, headRefName="feat/example", headRefOid=self.oid,
                             headRepositoryOwner={"login": "fork"}, headRepository={"name": "blog-writer"},
                             author={"login": "contributor"}, state="OPEN", baseRefName="main",
                             title="'quoted' \"$HOME\" `code` $(touch interpolated) \\u2028\u200b\u2028\u2029🌐\n\n",
                             url="https://" + self.origin + "/pull/6\n\n")

    def answer(self, argv, stdout):
        self.answers[json.dumps(argv)] = stdout
        (self.root / "answers").write_text(json.dumps(self.answers))

    def calls(self):
        return [json.loads(line) for line in (self.root / "calls").read_text().splitlines()]

    def execute(self, command, preview_json=None, **literals):
        for word, value in literals.items():
            for prefix in ("", "github.com/"):
                command = command.replace(f"'{prefix}{word}'", f"'{prefix}{value}'")
        if preview_json is not None:
            command = command.replace("\nAPPROVED_PREVIEW_JSON\n", f"\n{preview_json}\n")
        return subprocess.run([*self.shell, "-c", command], cwd=self.root, env=self.env,
                              capture_output=True)

    def fence(self, number, **literals):
        (command,) = re.findall(r"```bash\n(.*?)\n```", step(self.skill, number), re.S)
        return self.execute(command, **literals)

    def read_metadata(self):
        raw = json.dumps(self.metadata, indent=2, ensure_ascii=False).replace("🌐", r"\ud83c\udf10") + " \r\n\n"
        self.answer(self.view, raw)
        result = self.fence(1, PR_NUMBER=self.number)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         dict(repo=self.repo, number=6, isCrossRepository=self.metadata["isCrossRepository"]))
        self.assertEqual(self.calls(), [*self.preflight, self.view])

    def preview(self):
        self.read_metadata()
        result = self.fence(3, PR_NUMBER=self.number, VALIDATED_REPO=self.repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [*self.preflight, self.view, *self.preflight, self.view, self.heads])
        action = dict(repo=self.repo, number=6, fork="fork/blog-writer", headRefName="feat/example",
                      headRefOid=self.oid, baseRefName="main", branch=self.branch, author="contributor",
                      title=self.metadata["title"],
                      pointer="Adopted into the base repo as <new_pr_url> so the policy reviewer can run — fork PRs "
                              "are skipped by the reviewer's fork-guard. Leaving this PR open; close it whenever "
                              "you like, it's your call.",
                      body="Adopted from #6 by @contributor (fork fork/blog-writer).\n\n"
                           "Carries the contributor's original commits unchanged — authorship is preserved. "
                           f"As a same-repo PR, it gets the policy review.\n\nOriginal PR: {self.metadata['url']}")
        preview = json.loads(result.stdout)
        self.assertEqual(preview, action)
        return preview, result.stdout.decode("utf-8").rstrip("\n")

    def inspect(self, number, expected):
        self.answer(self.status, '{"reviewDecision":"APPROVED"}')
        self.answer(self.diff, "fixture diff")
        start = len(self.calls())
        for command in COMMAND.findall(step(self.skill, number)):
            result = self.execute(command, PR_NUMBER=self.number, VALIDATED_REPO=self.repo)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[start:], expected)

    def test_same_repo_status(self):
        self.metadata["isCrossRepository"] = False
        self.read_metadata()
        self.inspect(2, [self.status])

    def test_installed_metadata_uses_its_own_release_sibling(self):
        self.read_metadata()
        tessl = self.root / ".tessl/plugins/jbaruch/coding-policy/skills"
        acr = self.root / ".codex/skills"
        adopt = acr / "acr__jbaruch__coding-policy__adopt-fork-pr"
        release = acr / "acr__jbaruch__coding-policy__release"
        shutil.copytree(tessl / "adopt-fork-pr", adopt)
        shutil.copytree(tessl / "release", release)
        command = f'bash "{adopt.relative_to(self.root)}/adopt.sh" metadata \'{self.number}\''

        start = len(self.calls())
        result = self.execute(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(repo=self.repo, number=6, isCrossRepository=True))
        self.assertEqual(self.calls()[start:], [*self.preflight, self.view])

        release.rename(acr / "release")
        start = len(self.calls())
        result = self.execute(command)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"acr__jbaruch__coding-policy__release/origin-repo.py", result.stderr)
        self.assertIn(b"reinstall the plugin", result.stderr)
        self.assertEqual(self.calls()[start:], self.preflight[:1])

    def test_metadata_requires_valid_json_for_the_requested_pr(self):
        for raw in ("", "{}", '{"number":7,"isCrossRepository":true}', '{"number":6,"isCrossRepository":null}',
                    '{"number":6,"isCrossRepository":true,"title":"\udcff"}'):
            with self.subTest(raw=raw):
                self.answer(self.view, raw)
                result = self.fence(1, PR_NUMBER=self.number)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"could not classify PR #6", result.stderr)

    def test_fork_just_inspect(self):
        self.preview()
        self.inspect(3, [self.diff, self.status])

    def test_approved_fence_sends_the_displayed_action(self):
        action, preview_json = self.preview()
        ref = "refs/heads/" + self.branch
        url = "https://" + self.origin + "/pull/99"
        fetch = ["git", "fetch", "--no-tags", "origin", "refs/pull/6/head"]
        push = ["git", "push", "--no-follow-tags", "--force-with-lease=" + ref + ":", "origin", self.oid + ":" + ref]
        create = ["gh", "pr", "create", "--repo", self.origin, "--base", "main", "--head", self.branch,
                  "--title", action["title"], "--body", action["body"]]
        comment = ["gh", "pr", "comment", self.number, "--repo", self.origin, "--body",
                   action["pointer"].replace("<new_pr_url>", url)]
        verify = ["git", "rev-parse", "--verify", "FETCH_HEAD"]
        comments = ["gh", "pr", "view", self.number, "--repo", self.origin, "--json", "comments"]
        lookup = ["gh", "pr", "list", "--repo", self.origin, "--head", self.branch, "--state", "open",
                  "--json", "url,isCrossRepository,headRefName,headRefOid,baseRefName"]
        for cmd in (fetch, push, comment):
            self.answer(cmd, "")
        self.answer(verify, self.oid)
        self.answer(create, url)
        existing = dict(url=url, isCrossRepository=False, headRefName=self.branch, headRefOid=self.oid,
                        baseRefName="main")
        branch_head = self.oid + "\t" + ref
        for head, prs, linked, expected in (
            ("", [], [], [fetch, verify, push, create, comments, comment]),
            (branch_head, [], [], [lookup, create, comments, comment]),
            (branch_head, [existing], [], [lookup, comments, comment]),
            (branch_head, [existing], [{"body": comment[-1]}], [lookup, comments]),
        ):
            self.answer(self.heads, head)
            self.answer(lookup, json.dumps(prs))
            self.answer(comments, json.dumps({"comments": linked}))
            start = len(self.calls())
            result = self.fence(4, PR_NUMBER=self.number, VALIDATED_REPO=self.repo, preview_json=preview_json)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["state"], "already-adopted" if prs else "adopted")
            self.assertEqual(self.calls()[start:], [*self.preflight, self.view, self.heads, *expected])
        self.assertFalse((self.root / "interpolated").exists())

    def test_rejects_malformed_approved_preview_before_any_write(self):
        action, preview_json = self.preview()
        invalid = ("", "[]", preview_json + "\n{}", json.dumps(dict(action, title="NUL\0")),
                   json.dumps(action).replace('"number": 6', '"number": +6'))
        for value in invalid:
            with self.subTest(value=value):
                start = len(self.calls())
                result = self.fence(4, PR_NUMBER=self.number, VALIDATED_REPO=self.repo, preview_json=value)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"invalid approved preview JSON", result.stderr)
                self.assertEqual(self.calls()[start:], self.preflight)

    def test_rejects_malformed_current_metadata_before_any_write(self):
        _, preview_json = self.preview()
        raw = json.dumps(dict(self.metadata, title="fixture title"))
        for title in ('"\udcff"', r'"\udcff"', r'"\u0000"', "NaN"):
            with self.subTest(title=title):
                self.answer(self.view, raw.replace('"fixture title"', title, 1))
                for number in (3, 4):
                    start = len(self.calls())
                    result = self.fence(number, PR_NUMBER=self.number, VALIDATED_REPO=self.repo,
                                        preview_json=preview_json)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, b"")
                    self.assertIn(b"could not read PR #6", result.stderr)
                    self.assertEqual(self.calls()[start:], [*self.preflight, self.view])


class ZshDocumentedCommandsTest(DocumentedCommandsTest):
    shell = ["zsh", "-f"]

    @classmethod
    def setUpClass(cls):
        if shutil.which(cls.shell[0]) is None:
            raise RuntimeError("Install Zsh and put zsh on PATH to run the documented command tests.")


if __name__ == "__main__":
    unittest.main()
