"""AutoSE review job (stage 6): the independent reviewer (ADR-008).

Runs in its own job with a read-only contents token. The agent sees only the issue,
the PR diff and the repository at the PR head. Plain code posts the review and, if
changes are requested, starts the next round with workflow_dispatch (ADR-002).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents import HAIKU, SONNET, Policy, run_agent  # noqa: E402
from common import (OUT, REPO, State, comment, env, extract, fetch_issue, gh,  # noqa: E402
                    load_state, prompt, redact, save_state, take_github_token,
                    wrap_untrusted, write_out)

MAX_SEND_BACKS = 2
ISSUE_BUDGET_USD = 1.00
ROWS = ["R1", "R2", "R3", "R4", "R5", "R6"]


def validate(text: str) -> tuple[dict, list[str]]:
    """Code enforces the rubric rules; the model only fills it in (ADR-007/008)."""
    block = extract(text, "BEGIN_REVIEW", "END_REVIEW")
    if not block:
        return {}, ["no BEGIN_REVIEW/END_REVIEW block"]
    block = block.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(block)
    except json.JSONDecodeError as exc:
        return {}, [f"review is not valid JSON ({exc})"]
    errs = []
    rows = {r.get("id"): r for r in data.get("rubric", []) if isinstance(r, dict)}
    for rid in ROWS:
        r = rows.get(rid)
        if not r:
            errs.append(f"rubric row {rid} missing")
            continue
        if r.get("result") not in ("pass", "fail", "n/a"):
            errs.append(f"{rid} result must be pass/fail/n/a")
        if len(str(r.get("evidence", "")).strip()) < 10:
            errs.append(f"{rid} has no concrete evidence")
    verdict = data.get("verdict")
    fails = [rid for rid in ROWS if rows.get(rid, {}).get("result") == "fail"]
    if verdict not in ("APPROVE", "CHANGES_CODE", "CHANGES_APPROACH"):
        errs.append("verdict must be APPROVE, CHANGES_CODE or CHANGES_APPROACH")
    elif verdict == "APPROVE" and fails:
        errs.append(f"APPROVE is not allowed when rows fail: {fails}")
    elif verdict != "APPROVE" and not fails:
        errs.append(f"{verdict} needs at least one failed rubric row")
    elif verdict == "CHANGES_APPROACH" and "R1" not in fails:
        errs.append("CHANGES_APPROACH requires R1 to fail")
    if verdict in ("CHANGES_CODE", "CHANGES_APPROACH"):
        for f in data.get("findings", []):
            if not f.get("file") or not isinstance(f.get("line"), int):
                errs.append("every finding needs a file and an integer line")
                break
        if not data.get("findings"):
            errs.append("requesting changes needs at least one finding")
    return data, errs


def render(data: dict, model: str, variant: str, cost: float, round_: int) -> str:
    icon = {"pass": "✅", "fail": "❌", "n/a": "➖"}
    rows = "\n".join(
        f"| {r.get('id')} | {r.get('check', '')} | {icon.get(r.get('result'), '?')} {r.get('result')} "
        f"| {str(r.get('evidence', '')).replace('|', '/')[:400]} |"
        for r in data.get("rubric", []))
    finds = "\n".join(
        f"- **{f.get('severity', '?')}** `{f.get('file')}:{f.get('line')}`: {f.get('problem')}"
        f"{' Suggestion: ' + f['suggestion'] if f.get('suggestion') else ''}"
        for f in data.get("findings", [])) or "_none_"
    v = data.get("verdict", "INVALID")
    head = {"APPROVE": "✅ APPROVE", "CHANGES_CODE": "🛠️ CHANGES REQUESTED (code)",
            "CHANGES_APPROACH": "🧭 CHANGES REQUESTED (approach)"}.get(v, "⚠️ INVALID REVIEW")
    return (f"## 🔍 Independent review: {head}\n\n{data.get('summary', '')}\n\n"
            f"| # | Check | Result | Evidence |\n|---|---|---|---|\n{rows}\n\n"
            f"### Findings\n{finds}\n\n"
            f"<sub>Reviewer: `{model}` (variant: {variant}) · round {round_}/2 · cost ${cost:.3f} · "
            f"saw only the issue, the diff and the repo at the PR head. "
            f"A human makes the merge decision.</sub>\n")


def main() -> int:
    take_github_token()
    repo = Path(env("AUTOSE_REPO_DIR", ".")).resolve()
    issue_no = int(env("AUTOSE_ISSUE"))
    pr = env("AUTOSE_PR")
    mode = env("AUTOSE_MODE", "full")          # full | review_only (seeded PRs, experiments)
    variant = env("AUTOSE_VARIANT", "default")  # default | same_model | with_spec
    run_id = env("GITHUB_RUN_ID", "local")
    st = load_state(issue_no) if mode == "full" else None
    if mode == "full" and not pr and st and st.pr:
        pr = str(st.pr)
    round_ = st.round if st else int(env("AUTOSE_ROUND", "0") or 0)

    inp = OUT.parent / "review_in"
    inp.mkdir(parents=True, exist_ok=True)
    iss = fetch_issue(issue_no)
    (inp / "issue.md").write_text(wrap_untrusted(iss.get("title", ""), iss.get("body", "")))
    (inp / "diff.patch").write_text(gh(["pr", "diff", pr]))
    write_out("review_input_issue.md", (inp / "issue.md").read_text())

    model = HAIKU if variant == "same_model" else SONNET
    system = prompt("reviewer.md", ISSUE_FILE=str(inp / "issue.md"), DIFF_FILE=str(inp / "diff.patch"))
    if variant == "with_spec":       # Experiment B only: deliberately breaks the default rule
        src = st.last_build_run if st else env("AUTOSE_SPEC_RUN")
        if src:
            gh(["run", "download", str(src), "-n", "autose-build", "-D", str(inp / "b")], check=False)
        spec = inp / "b" / "spec.md"
        if spec.exists():
            (inp / "spec.md").write_text(spec.read_text())
            system += f"\n\nExperiment variant: you may also read the pipeline's spec at `{inp / 'spec.md'}`."

    asked = {"n": 0}

    def followup(text: str) -> str | None:
        _, errs = validate(text)
        if errs and asked["n"] < 1:
            asked["n"] += 1
            return ("Your review was rejected by the automatic rubric check: " + "; ".join(errs) +
                    ". Output the corrected review between BEGIN_REVIEW and END_REVIEW.")
        return None

    remaining = ISSUE_BUDGET_USD - (st.cost_usd if st else 0.0)
    policy = Policy(tools=["Read", "Grep", "Glob", "Bash"], read_roots=[repo, inp], bash_allowed=True)
    r = run_agent(system, "Review this pull request.", model=model, policy=policy, cwd=repo,
                  max_turns=40, budget_usd=max(0.05, min(0.40, remaining)), effort="medium",
                  timeout_s=600, followups=[followup])
    write_out("logs/review.txt", f"model={model} cost=${r.cost_usd:.4f} turns={r.turns} ok={r.ok} "
              f"err={r.error}\ndenied: {r.denials}\n\n" + "\n".join(r.transcript) +
              f"\n\n--- final ---\n{r.text}")
    data, errs = validate(r.text)
    if errs:
        data = data or {}
        data.setdefault("summary", "")
        data["summary"] = (f"⚠️ The reviewer's output failed the rubric check twice ({'; '.join(errs)}). "
                           "Treat this review as invalid; a human must review.") + data["summary"]
        data["verdict"] = "INVALID"
    data["model"], data["variant"], data["cost_usd"] = model, variant, r.cost_usd
    write_out("review.json", json.dumps(data, indent=2))
    md = render(data, model, variant, r.cost_usd, round_)
    write_out("review.md", md)
    gh(["pr", "review", pr, "--comment", "--body-file", str(OUT / "review.md")])

    if mode != "full" or not st:
        print(f"review-only mode: verdict {data['verdict']}")
        return 0

    st.add_cost(r.cost_usd, r.tokens_in, r.tokens_out)
    st.last_review_run = run_id
    st.note("review", data["verdict"], f"{model} ${r.cost_usd:.3f}")
    verdict = data["verdict"]
    if verdict == "APPROVE":
        st.status, st.stage = "approved", "done"
        st.passed.append("review")
        comment(issue_no, f"✅ The independent reviewer approved PR #{pr}. "
                          "**A human now decides whether to merge.**")
    elif verdict in ("CHANGES_CODE", "CHANGES_APPROACH") and st.round < MAX_SEND_BACKS \
            and st.cost_usd < ISSUE_BUDGET_USD - 0.10:
        start = "design" if verdict == "CHANGES_APPROACH" else "implement"
        st.status = "changes_requested"
        st.note("review", "send back", f"to {start}, round {st.round + 1}")
        save_state(st)
        gh(["workflow", "run", "autose.yml", "--ref", "master", "-f", f"issue={issue_no}",
            "-f", f"start_from={start}", "-f", f"round={st.round + 1}"])
    else:
        why = ("the review was invalid" if verdict == "INVALID" else
               "both review rounds are used" if st.round >= MAX_SEND_BACKS else "the budget is nearly used")
        st.status, st.stage = "rounds_used", "done"
        comment(issue_no, f"⏹️ AutoSE is handing PR #{pr} to a human because {why}. "
                          f"The last review is on the PR. **A human decides whether to merge or close.**")
    save_state(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
