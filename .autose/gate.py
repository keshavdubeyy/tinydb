"""AutoSE gate job (ADR-010): decides whether a run may start. No AI involved."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import comment, env, gh, set_output, take_github_token  # noqa: E402

TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
ACTIVE = {"in_progress", "queued", "waiting", "pending", "requested"}


def out(proceed: bool, issue: int | str = "", start_from: str = "requirements", round_: str = "0",
        mode: str = "full", pr: str = "", variant: str = "default") -> int:
    for k, v in dict(proceed=str(proceed).lower(), issue=issue, start_from=start_from, round=round_,
                     mode=mode, pr=pr, variant=variant).items():
        set_output(k, str(v))
    print(f"gate: proceed={proceed} issue={issue} start_from={start_from} round={round_} mode={mode}")
    return 0


def other_active_runs(issue: int) -> list[dict]:
    own = env("GITHUB_RUN_ID")
    runs = json.loads(gh(["run", "list", "--workflow", "autose.yml", "-L", "30",
                          "--json", "databaseId,status,displayTitle,url"], check=False) or "[]")
    return [r for r in runs if r["status"] in ACTIVE and str(r["databaseId"]) != own
            and re.search(rf"#{issue}\b", r.get("displayTitle", ""))]


def main() -> int:
    take_github_token()
    name = env("GITHUB_EVENT_NAME")
    ev = json.loads(Path(env("GITHUB_EVENT_PATH")).read_text())

    if name == "workflow_dispatch":
        i = ev.get("inputs", {})
        return out(True, i.get("issue", ""), i.get("start_from", "requirements"), i.get("round", "0"),
                   i.get("mode", "full"), i.get("pr", ""), i.get("variant", "default"))

    if name == "issues":
        if ev.get("label", {}).get("name") != "autose":
            return out(False)
        issue = ev["issue"]["number"]
        start, pr = "requirements", ""
    elif name == "issue_comment":
        c = ev["comment"]
        body = (c.get("body") or "").strip()
        if not body.startswith("/autose") or c.get("user", {}).get("type") == "Bot":
            return out(False)
        issue = ev["issue"]["number"]
        if c.get("author_association") not in TRUSTED:
            comment(issue, f"Thanks @{c['user']['login']}! AutoSE runs need a maintainer's approval. "
                           "A maintainer can add the `autose` label to start a run.")
            gh(["label", "create", "autose-requested", "--color", "fbca04", "--force",
                "--description", "Someone asked AutoSE to run; waiting for a maintainer"], check=False)
            gh(["issue", "edit", str(issue), "--add-label", "autose-requested"], check=False)
            return out(False)
        start, pr = "requirements", ""
        if "pull_request" in ev["issue"]:
            head = json.loads(gh(["pr", "view", str(issue), "--json", "headRefName"]))["headRefName"]
            m = re.fullmatch(r"autose/issue-(\d+)", head)
            if not m:
                comment(issue, "`/autose` on a pull request only works on AutoSE's own PRs.")
                return out(False)
            pr, issue, start = str(ev["issue"]["number"]), int(m.group(1)), "review"
        if "restart" in body:
            for r in other_active_runs(issue):
                gh(["run", "cancel", str(r["databaseId"])], check=False)
    else:
        return out(False)

    busy = other_active_runs(issue)
    if busy and "restart" not in (ev.get("comment", {}) or {}).get("body", ""):
        comment(issue, f"⏳ An AutoSE run for this issue is already in progress ({busy[0]['url']}). "
                       "Your request is queued and will start when it finishes. To cancel the "
                       "current run and start fresh, a maintainer can comment `/autose restart`.")
    return out(True, issue, start, "0", "full", pr)


if __name__ == "__main__":
    sys.exit(main())
