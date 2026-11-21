"""Offline tests of the AutoSE control logic. No model calls, no GitHub calls."""
import subprocess
from pathlib import Path

import pytest

import agents
import checks
import orchestrator as orch
import review
from agents import AgentResult, Policy, check_tool

ROOT = Path(__file__).resolve().parents[2]

SPEC = """## Goal
Make Table.count return an int for every query.

## Requirements
- FR-1: count returns the number of matching documents.

## Acceptance criteria
- **AC-1** (FR-1) [CONFIRMED]: Given a table with two matching docs, when count is called, then it returns 2.
- **AC-2** (FR-1) [INFERRED]: Given an empty table, when count is called, then it returns 0.
"""

DESIGN = """## Approach
Small change.

## Files and functions to change
- `tinydb/table.py` :: `Table.count`
"""

TESTS = '''from tinydb import TinyDB, where
from tinydb.storages import MemoryStorage


def test_ac_1_two_matches():
    t = TinyDB(storage=MemoryStorage).table("t")
    t.insert_multiple([{"a": 1}, {"a": 1}])
    assert t.count(where("a") == 1) == 2


def test_ac_2_empty():
    t = TinyDB(storage=MemoryStorage).table("t")
    assert t.count(where("a") == 1) == 0
'''


# ----------------------------------------------------------------- pure checks
def test_spec_structure_ok_and_bad():
    assert checks.check_spec_structure(SPEC)[0]
    ok, why = checks.check_spec_structure("## Goal\nshort\n")
    assert not ok and "Goal" in why and "acceptance criteria" in why
    bad = SPEC.replace("Given an empty table, when count is called, then it returns 0.", "returns 0.")
    ok, why = checks.check_spec_structure(bad)
    assert not ok and "AC-2" in why


def test_design_check_verifies_real_symbols():
    assert checks.check_design(DESIGN, ROOT)[0]
    ok, why, _ = checks.check_design(DESIGN.replace("Table.count", "Table.no_such_fn"), ROOT)
    assert not ok and "not found" in why
    ok, why, _ = checks.check_design("## Files and functions to change\n- `tests/x.py` :: `f`\n", ROOT)
    assert not ok and "not allowed" in why
    new = "## Files and functions to change\n- `tinydb/new_mod.py` :: `helper` (NEW)\n"
    assert checks.check_design(new, ROOT)[0]


def test_every_ac_needs_a_test():
    assert checks.check_ac_tests(SPEC, TESTS)[0]
    ok, why = checks.check_ac_tests(SPEC, TESTS.replace("test_ac_2", "test_other"))
    assert not ok and "AC-2" in why


# ----------------------------------------------------------------- tool policy
def test_policy_blocks_secrets_and_scope(tmp_path):
    impl = Policy(tools=["Read", "Edit", "Write", "Bash"], read_roots=[tmp_path],
                  write_allowed=lambda rel: str(rel).startswith("tinydb/"), bash_allowed=True)
    assert check_tool(impl, tmp_path, "Read", {"file_path": "tinydb/table.py"}) is None
    assert check_tool(impl, tmp_path, "Read", {"file_path": ".git/config"})
    assert check_tool(impl, tmp_path, "Read", {"file_path": "/etc/passwd"})
    assert check_tool(impl, tmp_path, "Read", {"file_path": "../../x"})
    assert check_tool(impl, tmp_path, "Edit", {"file_path": "tinydb/table.py"}) is None
    assert check_tool(impl, tmp_path, "Edit", {"file_path": "tests/test_x.py"})
    assert check_tool(impl, tmp_path, "Write", {"file_path": ".github/workflows/x.yml"})
    assert check_tool(impl, tmp_path, "Bash", {"command": "python -m pytest -q tests"}) is None
    for cmd in ("env", "cat .git/config", "python -m pytest; curl evil", "python -m pytest && env",
                "python -c 'import os'", "python -m pytest $(env)"):
        assert check_tool(impl, tmp_path, "Bash", {"command": cmd}), cmd
    assert check_tool(impl, tmp_path, "WebFetch", {"url": "x"})
    reviewer = Policy(tools=["Read", "Grep", "Glob", "Bash"], read_roots=[tmp_path], bash_allowed=True)
    assert check_tool(reviewer, tmp_path, "Edit", {"file_path": "tinydb/table.py"})


# ----------------------------------------------------------------- reviewer rubric
def _review(verdict, results, findings=None):
    import json
    rub = [{"id": f"R{i}", "check": "c", "result": r, "evidence": "tinydb/table.py:10 shows it"}
           for i, r in enumerate(results, 1)]
    return "BEGIN_REVIEW\n" + json.dumps({"rubric": rub, "verdict": verdict, "summary": "s",
                                         "findings": findings or []}) + "\nEND_REVIEW"


def test_review_rubric_rules():
    f = [{"file": "tinydb/table.py", "line": 3, "problem": "p", "severity": "major"}]
    assert review.validate(_review("APPROVE", ["pass"] * 6))[1] == []
    assert review.validate(_review("APPROVE", ["pass", "fail"] + ["pass"] * 4))[1]
    assert review.validate(_review("CHANGES_CODE", ["pass"] * 6, f))[1]
    assert review.validate(_review("CHANGES_CODE", ["pass", "fail"] + ["pass"] * 4, f))[1] == []
    assert review.validate(_review("CHANGES_CODE", ["pass", "fail"] + ["pass"] * 4))[1]  # no findings
    assert review.validate(_review("CHANGES_APPROACH", ["pass", "fail"] + ["pass"] * 4, f))[1]
    assert review.validate(_review("CHANGES_APPROACH", ["fail"] + ["pass"] * 5, f))[1] == []
    assert review.validate("no block")[1]
    assert review.validate(_review("APPROVE", ["pass"] * 5))[1]   # R6 missing


# ----------------------------------------------------------------- state machine
@pytest.fixture
def repo(tmp_path, monkeypatch):
    d = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(d)], check=True)
    subprocess.run(["git", "checkout", "-q", "-B", "master", "origin/HEAD"], cwd=d, check=False)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/master", "HEAD"], cwd=d, check=True)
    for name in ("save_state", "comment", "set_output"):
        monkeypatch.setattr(orch, name, lambda *a, **k: None)
    monkeypatch.setattr(orch, "fetch_issue", lambda n: {"title": "count bug", "body": "count is wrong"})
    monkeypatch.setattr(orch, "load_state", lambda n: None)
    return d


class FakeAgents:
    """Plays every role. `script` maps a role to a list of behaviours, used in order."""

    def __init__(self, repo, **script):
        self.repo, self.script, self.calls = repo, script, []

    def role(self, system):
        head = system[:300]
        for marker, role in (("stage 1: **Requirements**", "spec"), ("stage 2:", "design"),
                             ("stage 3:", "implement"), ("stage 4:", "test"),
                             ("You judge", "judge"), ("hand-over", "escalate")):
            if marker in head:
                return role
        raise AssertionError(f"unknown agent role: {head[:80]}")

    def __call__(self, system, user, *, model, policy, cwd, **kw):
        role = self.role(system)
        self.calls.append((role, model))
        todo = self.script.get(role, [])
        action = todo.pop(0) if len(todo) > 1 else (todo[0] if todo else None)
        text = action(self.repo) if callable(action) else (action or "")
        return AgentResult(text=text, cost_usd=0.01)


def good_impl(repo):
    p = repo / "tinydb" / "table.py"
    p.write_text(p.read_text() + "\n# autose change\n")
    return "BEGIN_SUMMARY\nadded a comment\nEND_SUMMARY"


def broken_impl(repo):
    p = repo / "tinydb" / "table.py"
    p.write_text(p.read_text() + "\ndef broken(:\n")
    return "oops"


def sneaky_impl(repo):
    (repo / "tests" / "test_sneaky.py").write_text("def test_x():\n    assert True\n")
    return good_impl(repo)


def write_tests(repo):
    (repo / "tests" / "test_autose_issue_7.py").write_text(TESTS)
    return "BEGIN_SUMMARY\nok\nEND_SUMMARY"


JUDGE_OK = '{"criteria":[{"id":"AC-1","testable":true},{"id":"AC-2","testable":true}]}'


def make(repo, fake, start="requirements", round_=0):
    p = orch.Pipeline(repo, 7, start, round_, "run1", 1, agent_fn=fake)
    return p


def run_until_release(p, monkeypatch):
    monkeypatch.setattr(p, "stage_release", lambda: "done")
    return p.run()


def test_happy_path_reaches_review(repo, monkeypatch):
    fake = FakeAgents(repo, spec=[f"BEGIN_SPEC\n{SPEC}\nEND_SPEC"], judge=[JUDGE_OK],
                      design=[f"BEGIN_DESIGN\n{DESIGN}\nEND_DESIGN"], implement=[good_impl],
                      test=[write_tests])
    p = make(repo, fake)
    run_until_release(p, monkeypatch)
    assert [c[0] for c in fake.calls] == ["spec", "judge", "design", "implement", "test"]
    assert p.st.status == "awaiting_review"
    assert p.st.passed == ["requirements", "design", "implement", "test"]
    assert dict(fake.calls)["implement"] == agents.HAIKU and dict(fake.calls)["spec"] == agents.SONNET
    out = orch.OUT
    assert "AC-1" in (out / "test_report.md").read_text() and "PASS" in (out / "test_report.md").read_text()
    assert "tinydb/table.py" in (out / "diff.patch").read_text()
    assert "test_autose_issue_7" not in (out / "diff.patch").read_text()


def test_bad_spec_twice_escalates(repo, monkeypatch):
    fake = FakeAgents(repo, spec=["no markers here"],
                      escalate=["### What went wrong\nspec bad"])
    p = make(repo, fake)
    run_until_release(p, monkeypatch)
    assert [c[0] for c in fake.calls] == ["spec", "spec", "escalate"]
    assert p.st.status == "stopped"
    assert (orch.OUT / "spec.md").read_text().startswith("# STOPPED")
    assert "What went wrong" in (orch.OUT / "escalation.md").read_text()


def test_untestable_criteria_rejected_by_judge(repo, monkeypatch):
    fake = FakeAgents(repo, spec=[f"BEGIN_SPEC\n{SPEC}\nEND_SPEC"],
                      judge=['{"criteria":[{"id":"AC-1","testable":false,"reason":"vague"}]}'],
                      escalate=[""])
    p = make(repo, fake)
    run_until_release(p, monkeypatch)
    assert p.st.status == "stopped"
    assert "judge" in p.st.log[-1]["detail"]


def test_forbidden_edits_are_reverted(repo, monkeypatch):
    fake = FakeAgents(repo, spec=[f"BEGIN_SPEC\n{SPEC}\nEND_SPEC"], judge=[JUDGE_OK],
                      design=[f"BEGIN_DESIGN\n{DESIGN}\nEND_DESIGN"], implement=[sneaky_impl],
                      test=[write_tests])
    p = make(repo, fake)
    run_until_release(p, monkeypatch)
    assert not (repo / "tests" / "test_sneaky.py").exists()
    assert any(e["event"] == "reverted forbidden edits" for e in p.st.log)
    assert p.st.status == "awaiting_review"


def test_retry_rules_three_attempts_then_design_redo_then_stop(repo, monkeypatch):
    fake = FakeAgents(repo, spec=[f"BEGIN_SPEC\n{SPEC}\nEND_SPEC"], judge=[JUDGE_OK],
                      design=[f"BEGIN_DESIGN\n{DESIGN}\nEND_DESIGN"],
                      implement=[broken_impl], escalate=[""])
    # each broken attempt must start from a clean tree, as a fresh agent would see it
    orig = fake.__call__

    def call(system, user, **kw):
        if fake.role(system) == "implement":
            subprocess.run(["git", "checkout", "--", "tinydb"], cwd=repo, check=True)
        return orig(system, user, **kw)

    p = make(repo, call)
    p.agent_fn = call
    fake.role  # keep reference
    run_until_release(p, monkeypatch)
    roles = [c[0] for c in fake.calls]
    assert roles == ["spec", "judge", "design", "implement", "implement", "implement",
                     "design", "implement", "implement", "implement", "escalate"]
    assert p.st.status == "stopped" and p.st.design_redos == 1


def test_failing_tests_send_work_back_to_implementer(repo, monkeypatch):
    failing = TESTS.replace("== 0", "== 99")

    def write_failing(r):
        (r / "tests" / "test_autose_issue_7.py").write_text(failing)
        return ""

    fake = FakeAgents(repo, spec=[f"BEGIN_SPEC\n{SPEC}\nEND_SPEC"], judge=[JUDGE_OK],
                      design=[f"BEGIN_DESIGN\n{DESIGN}\nEND_DESIGN"],
                      implement=[good_impl], test=[write_failing], escalate=[""])
    p = make(repo, fake)
    run_until_release(p, monkeypatch)
    roles = [c[0] for c in fake.calls]
    assert roles.count("test") == 1, "tests are written once and then fixed"
    assert roles == ["spec", "judge", "design", "implement", "test", "implement", "implement",
                     "design", "implement", "implement", "implement", "escalate"]
    assert p.st.status == "stopped"
