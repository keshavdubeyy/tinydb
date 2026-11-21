"""AutoSE build job: a fixed state machine that runs stages 1-5 (ADR-002).

No LLM decides what happens next. Agents produce documents; this code runs each
stage's check and picks the next stage from fixed rules.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks  # noqa: E402
from agents import HAIKU, SONNET, AgentResult, Policy, run_agent  # noqa: E402
from common import (OUT, PROMPTS, REPO, State, comment, env, extract, fetch_issue,  # noqa: E402
                    gh, git, git_push, load_state, prompt, redact, save_state, set_output,
                    take_github_token, wrap_untrusted, write_out)

ISSUE_BUDGET_USD = 1.00          # ADR-009: per issue, all rounds together
AGENT_TIME_S = 15 * 60           # ADR-009: agent work per run
CI_WAIT_S = 20 * 60              # ADR-009: CI waited for separately
MAX_IMPL_ATTEMPTS = 3            # ADR-002
MAX_DESIGN_REDOS = 1             # ADR-002
BASE = "master"

# turns, timeout seconds, budget cap per call
LIMITS = {"spec": (25, 180, 0.25), "judge": (2, 90, 0.03), "design": (30, 180, 0.25),
          "implement": (40, 300, 0.20), "test": (30, 240, 0.15), "escalate": (2, 90, 0.03)}


class Stop(Exception):
    pass


class Pipeline:
    def __init__(self, repo: Path, issue: int, start_from: str, round_: int,
                 run_id: str, attempt: int, agent_fn=run_agent):
        self.repo = repo
        self.issue = issue
        self.start_from = start_from
        self.round = round_
        self.run_id = run_id
        self.attempt = attempt
        self.agent_fn = agent_fn
        self.branch = f"autose/issue-{issue}"
        self.test_file = f"tests/test_autose_issue_{issue}.py"
        self.spec = ""
        self.design = ""
        self.feedback = {"design": "", "implement": "", "test": ""}
        self.agent_seconds = 0.0
        self.calls = 0
        prev = load_state(issue) if REPO else None
        self.prev_build = prev.last_build_run if prev else ""
        fresh = start_from == "requirements" and attempt == 1
        self.st = State(issue=issue, branch=self.branch)
        if prev and not fresh:
            self.st = prev
        if prev:
            self.st.cost_total_usd = prev.cost_total_usd
        self.st.round = round_
        self.st.status = "running"
        self.st.runs.append(run_id)
        self.st.last_build_run = run_id

    # ------------------------------------------------------------ helpers
    def run_url(self) -> str:
        return f"https://github.com/{REPO}/actions/runs/{self.run_id}"

    def agent(self, stage: str, system: str, user: str, model: str, policy: Policy,
              effort: str) -> AgentResult:
        turns, timeout, cap = LIMITS[stage]
        remaining = ISSUE_BUDGET_USD - self.st.cost_usd
        if remaining < 0.02:
            raise Stop(f"budget used up (${self.st.cost_usd:.2f} of ${ISSUE_BUDGET_USD:.2f})")
        if self.agent_seconds > AGENT_TIME_S:
            raise Stop(f"agent time used up ({self.agent_seconds/60:.1f} of {AGENT_TIME_S/60:.0f} min)")
        t0 = time.time()
        r = self.agent_fn(system, user, model=model, policy=policy, cwd=self.repo,
                          max_turns=turns, budget_usd=min(cap, remaining), effort=effort,
                          timeout_s=int(min(timeout, AGENT_TIME_S - self.agent_seconds + 30)))
        self.agent_seconds += time.time() - t0
        self.calls += 1
        self.st.add_cost(r.cost_usd, r.tokens_in, r.tokens_out)
        write_out(f"logs/{self.calls:02d}-{stage}.txt",
                  f"model={model} cost=${r.cost_usd:.4f} turns={r.turns} ok={r.ok} err={r.error}\n"
                  f"denied tool calls: {r.denials}\n\n" + "\n".join(r.transcript) +
                  f"\n\n--- final text ---\n{r.text}")
        self.st.note(stage, "agent", f"{model} ${r.cost_usd:.3f} turns={r.turns}"
                     + (f" denied={len(r.denials)}" if r.denials else "")
                     + (f" error={r.error}" if not r.ok else ""))
        return r

    def read_policy(self) -> Policy:
        return Policy(tools=["Read", "Grep", "Glob"], read_roots=[self.repo])

    def commit(self, msg: str, paths: list[str]) -> None:
        git(["add", "--", *paths], cwd=self.repo)
        if git(["diff", "--cached", "--name-only"], cwd=self.repo).strip():
            git(["-c", "user.name=autose-bot", "-c",
                 "user.email=autose-bot@users.noreply.github.com",
                 "commit", "-q", "-m", msg], cwd=self.repo)
        if REPO:
            git_push(self.branch, cwd=self.repo)   # checkpoint (ADR-004); does not trigger CI

    def passed(self, stage: str) -> None:
        if stage not in self.st.passed:
            self.st.passed.append(stage)
        self.st.note(stage, "passed")
        save_state(self.st)

    # ------------------------------------------------------------ stage 1
    def stage_requirements(self) -> str:
        self.st.stage = "requirements"
        save_state(self.st)
        iss = fetch_issue(self.issue)
        issue_text = wrap_untrusted(iss.get("title", ""), iss.get("body", ""))
        write_out("issue.md", issue_text)
        system = prompt("planner_spec.md",
                        A1_SKILL=(PROMPTS / "a1_skill_condensed.md").read_text(encoding="utf-8"))
        fb, last, why = "", "", ""
        for _ in range(2):
            r = self.agent("spec", system, f"{issue_text}\n\nWrite the spec.{fb}", SONNET,
                           self.read_policy(), "medium")
            spec = extract(r.text, "BEGIN_SPEC", "END_SPEC")
            if not spec:
                why = "no BEGIN_SPEC/END_SPEC block in the answer"
            else:
                last = spec
                ok, why = checks.check_spec_structure(spec)
                if ok:
                    ok, why = self.judge(spec)
                if ok:
                    self.spec = spec
                    write_out("spec.md", spec)
                    self.st.note("requirements", "check", why)
                    return "design"
            self.st.note("requirements", "check failed", why)
            fb = (f"\n\nA previous draft was rejected by the automatic check: {why}\n"
                  f"Previous draft:\n{last}\nFix these problems.")
        write_out("spec.md", f"# STOPPED: spec did not pass its check\n\nReason: {why}\n\n---\n\n{last}")
        raise Stop(f"requirements check failed twice: {why}")

    def judge(self, spec: str) -> tuple[bool, str]:
        acs = checks.acceptance_criteria(spec)
        crit = "\n".join(f"- {i}: {t}" for i, t in acs)
        r = self.agent("judge", "You judge acceptance criteria. Reply with JSON only.",
                       prompt("judge_spec.md", CRITERIA=crit), HAIKU,
                       Policy(tools=[], read_roots=[]), "low")
        m = re.search(r"\{.*\}", r.text, re.S)
        try:
            data = json.loads(m.group(0)) if m else {}
            verdicts = {c["id"]: c for c in data.get("criteria", [])}
        except (json.JSONDecodeError, KeyError, TypeError):
            return False, "judge reply was not valid JSON"
        bad = [f"{i}: {verdicts.get(i, {}).get('reason', 'not rated')}"
               for i, _ in acs if not verdicts.get(i, {}).get("testable")]
        write_out("judge.json", json.dumps(data, indent=2))
        return (not bad, "judge: not testable -> " + "; ".join(bad) if bad
                else "judge: all criteria testable")

    # ------------------------------------------------------------ stage 2
    def stage_design(self) -> str:
        self.st.stage = "design"
        save_state(self.st)
        extra, last, why = "", "", ""
        for _ in range(2):
            fb = self.feedback["design"] + extra
            system = prompt("planner_design.md",
                            FEEDBACK=f"## Feedback to take into account\n\n{fb}" if fb else "")
            r = self.agent("design", system,
                           f"Spec:\n\n{self.spec}\n\nRead ARCHITECTURE.md first, then write the design.",
                           SONNET, self.read_policy(), "medium")
            design = extract(r.text, "BEGIN_DESIGN", "END_DESIGN")
            if not design:
                why = "no BEGIN_DESIGN/END_DESIGN block in the answer"
            else:
                last = design
                ok, why, _files = checks.check_design(design, self.repo)
                if ok:
                    self.design = design
                    write_out("design.md", design)
                    self.st.note("design", "check", why)
                    self.feedback["design"] = ""
                    return "implement"
            self.st.note("design", "check failed", why)
            extra = f"\n\nYour previous design was rejected: {why}\nPrevious design:\n{last}"
        write_out("design.md", f"# STOPPED: design did not pass its check\n\nReason: {why}\n\n---\n\n{last}")
        raise Stop(f"design check failed twice: {why}")

    # ------------------------------------------------------------ stage 3
    def stage_implement(self) -> str:
        self.st.stage = "implement"
        save_state(self.st)
        fb = self.feedback["implement"]
        system = prompt("implementer.md",
                        FEEDBACK=f"## Feedback from the previous attempt\n\n{fb}" if fb else "")
        policy = Policy(tools=["Read", "Grep", "Glob", "Edit", "Write", "Bash"],
                        read_roots=[self.repo],
                        write_allowed=lambda rel: str(rel).startswith("tinydb/"),
                        bash_allowed=True)
        r = self.agent("implement", system,
                       f"Spec:\n\n{self.spec}\n\nDesign:\n\n{self.design}\n\nMake the change.",
                       HAIKU, policy, "low")
        files = checks.changed_files(self.repo)
        bad = checks.implementer_violations(files)
        if bad:
            checks.revert_files(self.repo, bad)
            self.st.note("implement", "reverted forbidden edits", ", ".join(bad))
        src_changed = [f for f in checks.changed_files(self.repo) if f.startswith("tinydb/")]
        summary = extract(r.text, "BEGIN_SUMMARY", "END_SUMMARY") or r.text[-1500:]
        if not src_changed and "implement" not in self.st.passed:
            return self.on_failure("implement", "the implementer made no change to tinydb/. "
                                   f"Its summary: {summary}")
        ok, why = checks.check_build(self.repo)
        if not ok:
            return self.on_failure("implement", f"the code does not build/import:\n{why}")
        self.commit(f"autose(#{self.issue}): implementation (attempt {self.st.impl_attempts + 1})",
                    ["tinydb"])
        diff = git(["diff", f"origin/{BASE}...HEAD", "--", ".", f":(exclude){self.test_file}"],
                   cwd=self.repo)
        write_out("diff.patch", diff)
        write_out("implementer_summary.md", summary)
        self.passed("implement")
        return "test"

    # ------------------------------------------------------------ stage 4
    def run_tester(self, extra: str = "") -> None:
        policy = Policy(tools=["Read", "Grep", "Glob", "Write", "Bash"],
                        read_roots=[self.repo],
                        write_allowed=lambda rel: str(rel) == self.test_file,
                        bash_allowed=True)
        system = prompt("tester.md", TEST_FILE=self.test_file)
        self.agent("test", system, f"Spec:\n\n{self.spec}\n\nWrite {self.test_file}.{extra}",
                   HAIKU, policy, "low")
        bad = checks.tester_violations(checks.changed_files(self.repo), self.test_file)
        if bad:
            checks.revert_files(self.repo, bad)
            self.st.note("test", "reverted forbidden edits", ", ".join(bad))

    def stage_test(self) -> str:
        self.st.stage = "test"
        save_state(self.st)
        tf = self.repo / self.test_file
        if not tf.exists():
            self.run_tester(self.feedback["test"])
            self.feedback["test"] = ""
            for i in range(2):
                if not tf.exists():
                    ok, why = False, f"the tester did not create {self.test_file}"
                else:
                    ok, why = checks.check_ac_tests(self.spec, tf.read_text(encoding="utf-8"))
                if ok:
                    break
                self.st.note("test", "check failed", why)
                if i == 1:
                    write_out("test_report.md", f"# STOPPED: tests could not be written\n\nReason: {why}")
                    raise Stop(f"tester could not produce a test per criterion: {why}")
                self.run_tester(f"\n\nYour previous attempt was rejected: {why}. "
                                f"Fix the test file so every criterion has a test named test_ac_<n>_...")
            self.commit(f"autose(#{self.issue}): acceptance tests", [self.test_file])
            self.st.note("test", "tests written", checks.check_ac_tests(self.spec, tf.read_text())[1])
        ok, tail, results = checks.run_tests(self.repo, OUT / "junit.xml")
        self.write_test_report(ok, tail, results)
        if ok:
            self.passed("test")
            return "release"
        return self.on_failure("test", f"tests fail (tests are fixed; change the code):\n{tail[-2500:]}")

    def write_test_report(self, ok: bool, tail: str, results: dict) -> None:
        acs = checks.acceptance_criteria(self.spec)
        mod = Path(self.test_file).stem
        rows = []
        for ac_id, text in acs:
            n = ac_id.split("-")[1]
            hits = {k.split("::")[-1]: v for k, v in results.items()
                    if mod in k and re.match(rf"test_ac_{n}(_|$)", k.split("::")[-1])}
            res = ", ".join(f"`{k}` {v}" for k, v in hits.items()) or "no test"
            rows.append(f"| {ac_id} | {text[:90]} | {res} |")
        total = len(results)
        failed = sum(1 for v in results.values() if v == "failed")
        report = (f"# Test report (issue #{self.issue}, round {self.round})\n\n"
                  f"**Result: {'PASS' if ok else 'FAIL'}**: {total - failed} passed/skipped, "
                  f"{failed} failed, {total} total (full suite)\n\n"
                  f"| AC | Criterion | Tests |\n|---|---|---|\n" + "\n".join(rows) +
                  f"\n\n<details><summary>pytest output (tail)</summary>\n\n```\n{tail}\n```\n</details>\n")
        write_out("test_report.md", report)

    # ------------------------------------------------------------ stage 5
    def stage_release(self) -> str:
        self.st.stage = "release"
        save_state(self.st)
        sha = git(["rev-parse", "HEAD"], cwd=self.repo).strip()
        open_prs = json.loads(gh(["pr", "list", "--head", self.branch, "--state", "open",
                                  "--json", "number,url"]) or "[]")
        body = self.pr_body()
        write_out("pr.md", body)
        if open_prs:
            num, url = open_prs[0]["number"], open_prs[0]["url"]
            gh(["pr", "edit", str(num), "--body-file", str(OUT / "pr.md")])
        else:
            title = fetch_issue(self.issue).get("title", f"issue {self.issue}")
            url = gh(["pr", "create", "--base", BASE, "--head", self.branch,
                      "--title", f"autose: {title[:80]} (#{self.issue})",
                      "--body-file", str(OUT / "pr.md")]).strip().splitlines()[-1]
            num = int(url.rstrip("/").split("/")[-1])
        self.st.pr = num
        self.st.note("release", "PR open", url)
        save_state(self.st)
        ok, detail = self.wait_ci(sha)
        write_out("pr.md", body + f"\n\n---\nPR: {url}\nHead: `{sha}`\nCI: {detail}\n")
        if not ok:
            return self.on_failure("implement", f"CI failed on the PR:\n{detail[-2500:]}")
        self.passed("release")
        set_output("pr", str(num))
        set_output("head_sha", sha)
        return "done"

    def wait_ci(self, sha: str) -> tuple[bool, str]:
        if env("AUTOSE_SKIP_CI") == "1":
            return True, "skipped (AUTOSE_SKIP_CI=1)"
        gh(["workflow", "run", "ci-workflow.yml", "--ref", self.branch])
        deadline = time.time() + CI_WAIT_S
        run_id = None
        while time.time() < deadline:
            time.sleep(20)
            runs = json.loads(gh(["run", "list", "--workflow", "ci-workflow.yml", "--branch",
                                  self.branch, "--event", "workflow_dispatch", "-L", "10",
                                  "--json", "databaseId,headSha,status,conclusion"]) or "[]")
            mine = [r for r in runs if r["headSha"] == sha]
            if not mine:
                continue
            run_id = mine[0]["databaseId"]
            if mine[0]["status"] == "completed":
                url = f"https://github.com/{REPO}/actions/runs/{run_id}"
                if mine[0]["conclusion"] == "success":
                    self.st.note("release", "CI passed", url)
                    return True, f"passed ({url})"
                log = gh(["run", "view", str(run_id), "--log-failed"], check=False)
                self.st.note("release", "CI failed", url)
                return False, f"{mine[0]['conclusion']} ({url})\n{log[-2500:]}"
        return False, f"CI did not finish within {CI_WAIT_S // 60} minutes (run {run_id})"

    def pr_body(self) -> str:
        from checks import section
        stat = git(["diff", "--stat", f"origin/{BASE}...HEAD"], cwd=self.repo)
        return (f"## 🤖 AutoSE pull request for #{self.issue}\n\nCloses #{self.issue}\n\n"
                f"### Goal (from the spec)\n{section(self.spec, 'Goal')}\n\n"
                f"### Acceptance criteria\n{section(self.spec, 'Acceptance criteria')}\n\n"
                f"### Assumptions and open questions (please check these)\n"
                f"{section(self.spec, 'Assumptions') or '_none_'}\n\n"
                f"{section(self.spec, 'Open questions') or ''}\n\n"
                f"### What changed\n```\n{stat}\n```\n\n"
                f"### Pipeline\n- Review round: {self.round}/2 · implementation attempts: "
                f"{self.st.impl_attempts + 1} · design redos: {self.st.design_redos}\n"
                f"- Cost this attempt so far: ${self.st.cost_usd:.3f}\n"
                f"- Run and artifacts (spec.md, design.md, diff.patch, test_report.md): {self.run_url()}\n\n"
                f"> ⚠️ Generated by an automated pipeline. An independent reviewer will comment "
                f"below. **A human decides whether to merge.**\n")

    # ------------------------------------------------------------ rules (ADR-002)
    def on_failure(self, where: str, detail: str) -> str:
        self.st.impl_attempts += 1
        self.st.note(where, "failed", detail[:300])
        if self.st.impl_attempts < MAX_IMPL_ATTEMPTS:
            self.feedback["implement"] = detail
            return "implement"
        if self.st.design_redos < MAX_DESIGN_REDOS:
            self.st.design_redos += 1
            self.st.impl_attempts = 0
            self.feedback["design"] = (f"The previous design was implemented {MAX_IMPL_ATTEMPTS} times "
                                       f"and still failed. Last failure:\n{detail[-2000:]}\n"
                                       "Rethink the approach.")
            self.feedback["implement"] = detail
            return "design"
        raise Stop(f"still failing after {MAX_IMPL_ATTEMPTS} attempts and a design redo: {detail[-500:]}")

    # ------------------------------------------------------------ escalation (ADR-002)
    def escalate(self, reason: str) -> None:
        self.st.status = "stopped"
        self.st.note(self.st.stage, "STOPPED", reason)
        log = "\n".join(f"{e['t']} [{e['stage']}] {e['event']}: {e['detail']}" for e in self.st.log)
        log += (f"\n\nStop reason: {reason}\nIssue budget: ${ISSUE_BUDGET_USD:.2f} for all rounds; "
                f"spent so far: ${self.st.cost_usd:.3f}. (Per-call limits are not the issue budget.)")
        report = ""
        try:
            r = self.agent_fn("You write factual hand-over reports.", prompt("escalation.md", LOG=log),
                              model=HAIKU, policy=Policy(tools=[], read_roots=[]), cwd=self.repo,
                              max_turns=2, budget_usd=0.03, effort="low", timeout_s=90)
            self.st.add_cost(r.cost_usd, r.tokens_in, r.tokens_out)
            report = r.text if r.ok else ""
        except Exception:  # noqa: BLE001 - the report must never block the stop
            report = ""
        if "### What went wrong" not in report:
            report = (f"### What went wrong\n{reason}\n\n### Why\nSee the log.\n\n"
                      f"### Where / who is involved\nStage: {self.st.stage}\n\n"
                      f"### What was tried\n```\n{log[-2500:]}\n```\n\n"
                      "### Options for the maintainer\n1. Clarify the issue and re-run.\n"
                      "2. Fix by hand on the branch.\n\n### Trade-offs\n1. Costs another run.\n"
                      "2. Costs maintainer time.")
        text = (f"## 🛑 AutoSE stopped and needs a human (issue #{self.issue})\n\n{report}\n\n"
                f"Run and artifacts: {self.run_url()}")
        write_out("escalation.md", text)
        if REPO:
            comment(self.issue, text)
        save_state(self.st)

    # ------------------------------------------------------------ resume (ADR-004)
    def prepare(self) -> str:
        """Set up the branch and earlier documents. Returns the first stage to run."""
        fresh = self.start_from == "requirements" and self.attempt == 1
        git(["fetch", "-q", "origin", BASE], cwd=self.repo)
        remote_branch = git(["ls-remote", "--heads", "origin", self.branch], cwd=self.repo).strip()
        if fresh or not remote_branch:
            git(["checkout", "-q", "-B", self.branch, f"origin/{BASE}"], cwd=self.repo)
            if fresh:
                self.st.passed, self.st.impl_attempts, self.st.design_redos = [], 0, 0
                self.st.cost_usd = 0.0
                if remote_branch and REPO:   # a new attempt replaces the bot's old branch
                    url_push_force(self.branch, self.repo)
            if fresh:
                return "requirements"
        else:
            git(["fetch", "-q", "origin", self.branch], cwd=self.repo)
            git(["checkout", "-q", "-B", self.branch, f"origin/{self.branch}"], cwd=self.repo)
        # load the documents from the previous build run
        src = self.run_id if self.attempt > 1 else self.prev_build
        prev = OUT.parent / "prev"
        if src:
            gh(["run", "download", str(src), "-n", "autose-build", "-D", str(prev)], check=False)
        self.spec = (prev / "spec.md").read_text() if (prev / "spec.md").exists() else ""
        self.design = (prev / "design.md").read_text() if (prev / "design.md").exists() else ""
        if self.spec.startswith("# STOPPED"):
            self.spec = ""
        if self.design.startswith("# STOPPED"):
            self.design = ""
        if self.spec:
            write_out("spec.md", self.spec)
        if self.design:
            write_out("design.md", self.design)

        if self.attempt > 1:
            return self.resume_point()
        # review loop (ADR-002): start where the reviewer pointed
        self.st.impl_attempts, self.st.design_redos = 0, 0
        self.st.passed = [s for s in self.st.passed if s in ("requirements", "design")]
        review_fb = self.review_feedback()
        if self.start_from == "design":
            self.st.passed = ["requirements"]
            self.feedback["design"] = review_fb
        self.feedback["implement"] = review_fb
        if not self.spec:
            return "requirements"
        if not self.design and self.start_from != "requirements":
            return "design"
        return self.start_from

    def review_feedback(self) -> str:
        rr = self.st.last_review_run
        if not rr:
            return ""
        d = OUT.parent / "prev_review"
        gh(["run", "download", str(rr), "-n", "autose-review", "-D", str(d)], check=False)
        text = (d / "review.md").read_text() if (d / "review.md").exists() else ""
        data = {}
        if (d / "review.json").exists():
            try:
                data = json.loads((d / "review.json").read_text())
            except json.JSONDecodeError:
                data = {}
        r3 = next((r for r in data.get("rubric", []) if r.get("id") == "R3"), {})
        if r3.get("result") == "fail" and (self.repo / self.test_file).exists():
            git(["rm", "-q", self.test_file], cwd=self.repo)   # tests judged weak: Tester rewrites
            self.commit(f"autose(#{self.issue}): drop tests the reviewer judged weak", [self.test_file])
            self.feedback["test"] = f"\n\nThe reviewer judged the previous tests weak: {r3.get('evidence', '')}"
        return f"The independent reviewer requested changes:\n\n{text[-4000:]}"

    def resume_point(self) -> str:
        """Re-check the last passed stage before trusting it; restart the unfinished one."""
        order = ["requirements", "design", "implement", "test", "release"]
        passed = [s for s in order if s in self.st.passed]
        checks_ok = {
            "requirements": lambda: bool(self.spec) and checks.check_spec_structure(self.spec)[0],
            "design": lambda: bool(self.design) and checks.check_design(self.design, self.repo)[0],
            "implement": lambda: checks.check_build(self.repo)[0],
            "test": lambda: checks.run_tests(self.repo, OUT / "junit_resume.xml")[0],
        }
        while passed and not checks_ok.get(passed[-1], lambda: True)():
            self.st.note(passed[-1], "re-check failed on resume")
            passed.pop()
        self.st.passed = passed
        nxt = next((s for s in order if s not in passed), "release")
        notes = "\n".join(f"- {e['stage']}: {e['event']} {e['detail'][:200]}" for e in self.st.log[-15:])
        resume = (f"Resume note: a previous attempt of this run stopped during '{self.st.stage}'. "
                  f"What happened before:\n{notes}\nContinue from the last checked state.")
        self.feedback["implement"] = resume
        self.feedback["design"] = resume
        self.st.note(nxt, "resuming", f"last passed: {passed[-1] if passed else 'none'}")
        return nxt

    # ------------------------------------------------------------ main loop
    def run(self) -> int:
        handlers = {"requirements": self.stage_requirements, "design": self.stage_design,
                    "implement": self.stage_implement, "test": self.stage_test,
                    "release": self.stage_release}
        review = "false"
        try:
            stage = self.prepare()
            self.st.note(stage, "start", f"round {self.round}, run attempt {self.attempt}")
            save_state(self.st)
            while stage != "done":
                nxt = handlers[stage]()
                if stage in ("requirements", "design") and nxt != stage:
                    self.passed(stage)
                stage = nxt
            self.st.status = "awaiting_review"
            self.st.stage = "review"
            review = "true"
        except Stop as s:
            self.escalate(str(s))
        except Exception as exc:  # noqa: BLE001 - unexpected errors are escalated, not hidden
            self.escalate(f"unexpected error: {type(exc).__name__}: {redact(str(exc))[:800]}")
        save_state(self.st)
        set_output("review", review)
        set_output("round", str(self.round))
        return 0


def url_push_force(branch: str, repo: Path) -> None:
    from common import _GH_TOKEN, sh
    url = f"https://x-access-token:{_GH_TOKEN}@github.com/{REPO}.git"
    sh(["git", "push", "--force", url, f"HEAD:refs/heads/{branch}"], cwd=repo)


def main() -> int:
    take_github_token()
    repo = Path(env("AUTOSE_REPO_DIR", ".")).resolve()
    p = Pipeline(repo, int(env("AUTOSE_ISSUE")), env("AUTOSE_START_FROM", "requirements"),
                 int(env("AUTOSE_ROUND", "0") or 0), env("GITHUB_RUN_ID", "local"),
                 int(env("GITHUB_RUN_ATTEMPT", "1") or 1))
    return p.run()


if __name__ == "__main__":
    sys.exit(main())
