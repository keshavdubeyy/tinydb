"""Shared helpers: shell, git, gh, secrets redaction, run state (ADR-004)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"

STATUS_MARK = "<!-- autose-status -->"
STATE_RE = re.compile(r"<!-- autose-state: (\{.*?\}) -->", re.S)

# --------------------------------------------------------------------------
# Secrets: taken out of the environment at start-up so that agent
# subprocesses (which inherit os.environ) never see the GitHub token (ADR-005).
# --------------------------------------------------------------------------
_SECRETS: list[str] = []
_GH_TOKEN: str = ""


def take_github_token() -> None:
    """Remove the GitHub token from os.environ and keep it private to this module."""
    global _GH_TOKEN
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.pop(name, "")
        if val and not _GH_TOKEN:
            _GH_TOKEN = val
    if _GH_TOKEN:
        _SECRETS.append(_GH_TOKEN)
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        _SECRETS.append(key)


def redact(text: str) -> str:
    """Never let a secret reach an artifact, comment or log (ADR-009)."""
    for s in _SECRETS:
        if s and len(s) > 8:
            text = text.replace(s, "[REDACTED]")
    return text


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


REPO = env("GH_REPO") or env("GITHUB_REPOSITORY")
OUT = Path(env("AUTOSE_OUT", "/tmp/autose_out"))
OUT.mkdir(parents=True, exist_ok=True)


def sh(cmd: list[str], cwd: str | Path | None = None, check: bool = True,
       timeout: int = 600, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    if extra_env:
        e.update(extra_env)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=e)
    if check and p.returncode != 0:
        raise RuntimeError(redact(f"command failed ({p.returncode}): {' '.join(cmd)}\n"
                                  f"{p.stdout[-2000:]}\n{p.stderr[-2000:]}"))
    return p


def gh(args: list[str], check: bool = True, cwd: str | Path | None = None) -> str:
    """Run the gh CLI with the private token. Only plain code calls this (ADR-005)."""
    extra = {"GH_TOKEN": _GH_TOKEN} if _GH_TOKEN else {}
    if REPO:
        extra["GH_REPO"] = REPO
    return sh(["gh", *args], check=check, cwd=cwd, extra_env=extra).stdout


def git(args: list[str], cwd: str | Path = ".", check: bool = True) -> str:
    return sh(["git", *args], cwd=cwd, check=check).stdout


def git_push(branch: str, cwd: str | Path = ".") -> None:
    """Push with the token in the URL only for this call; nothing is stored in .git/config."""
    url = f"https://x-access-token:{_GH_TOKEN}@github.com/{REPO}.git"
    sh(["git", "push", url, f"HEAD:refs/heads/{branch}"], cwd=cwd)


def write_out(name: str, text: str) -> Path:
    p = OUT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(redact(text), encoding="utf-8")
    return p


def set_output(name: str, value: str) -> None:
    path = env("GITHUB_OUTPUT")
    line = f"{name}={value}\n"
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    else:
        print(f"[output] {line.strip()}")


def prompt(name: str, **subs: str) -> str:
    text = (PROMPTS / name).read_text(encoding="utf-8")
    for k, v in subs.items():
        text = text.replace("{" + k + "}", v)
    return text


def wrap_untrusted(title: str, body: str) -> str:
    def clean(s: str) -> str:
        return (s or "").replace("</untrusted_issue>", "&lt;/untrusted_issue&gt;")
    return f"<untrusted_issue>\nTitle: {clean(title)}\n\n{clean(body)}\n</untrusted_issue>"


def extract(text: str, begin: str, end: str) -> str | None:
    m = re.search(rf"^{begin}\s*$(.*?)^{end}\s*$", text or "", re.S | re.M)
    return m.group(1).strip() if m else None


# --------------------------------------------------------------------------
# Run state, kept in a status comment on the issue (ADR-004)
# --------------------------------------------------------------------------
STAGES = ["requirements", "design", "implement", "test", "release", "review"]


@dataclass
class State:
    issue: int
    status: str = "running"          # running | awaiting_review | approved | stopped | rounds_used
    round: int = 0                   # review send-backs used so far
    stage: str = "requirements"
    passed: list[str] = field(default_factory=list)
    impl_attempts: int = 0
    design_redos: int = 0
    cost_usd: float = 0.0            # this attempt (all rounds)
    cost_total_usd: float = 0.0      # ever, for reporting
    tokens_in: int = 0
    tokens_out: int = 0
    pr: int | None = None
    branch: str = ""
    last_build_run: str = ""
    last_review_run: str = ""
    runs: list[str] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)

    def note(self, stage: str, event: str, detail: str = "") -> None:
        entry = {"t": time.strftime("%H:%M:%S"), "stage": stage, "event": event,
                 "detail": redact(detail)[:600]}
        self.log.append(entry)
        print(f"[{entry['t']}] {stage}: {event} {detail[:300]}")

    def add_cost(self, usd: float, tin: int = 0, tout: int = 0) -> None:
        self.cost_usd += usd or 0.0
        self.cost_total_usd += usd or 0.0
        self.tokens_in += tin
        self.tokens_out += tout

    def to_json(self) -> str:
        d = asdict(self)
        d["log"] = d["log"][-40:]
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, s: str) -> "State":
        d = json.loads(s)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


def _find_status_comment(issue: int) -> tuple[int | None, str]:
    raw = gh(["api", f"repos/{REPO}/issues/{issue}/comments", "--paginate"], check=False)
    try:
        comments = json.loads(raw or "[]")
    except json.JSONDecodeError:
        comments = []
    for c in comments:
        if STATUS_MARK in (c.get("body") or "") and c.get("user", {}).get("type") == "Bot":
            return c["id"], c["body"]
    return None, ""


def load_state(issue: int) -> State | None:
    _, body = _find_status_comment(issue)
    m = STATE_RE.search(body)
    if not m:
        return None
    try:
        st = State.from_json(m.group(1))
        return st if st.issue == issue else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def render_status(st: State) -> str:
    icons = {s: ("✅" if s in st.passed else ("⏳" if s == st.stage else "·")) for s in STAGES}
    rows = "\n".join(f"| {i+1} | {s} | {icons[s]} |" for i, s in enumerate(STAGES))
    recent = "\n".join(f"- `{e['t']}` **{e['stage']}** {e['event']}: {e['detail'][:160]}"
                       for e in st.log[-8:])
    pr = f"#{st.pr}" if st.pr else "not yet"
    return (f"{STATUS_MARK}\n### 🤖 AutoSE status: `{st.status}`\n\n"
            f"Review round **{st.round}/2** · PR: {pr} · cost this attempt: "
            f"**${st.cost_usd:.3f}** / $1.00 · tokens in/out: {st.tokens_in}/{st.tokens_out}\n\n"
            f"| # | Stage | |\n|---|---|---|\n{rows}\n\n"
            f"<details><summary>Recent steps</summary>\n\n{recent}\n</details>\n\n"
            f"_A human decides whether to merge. The bot never merges._\n\n"
            f"<!-- autose-state: {st.to_json()} -->")


def save_state(st: State) -> None:
    body = redact(render_status(st))
    write_out("state.json", st.to_json())
    if not REPO:
        return
    cid, _ = _find_status_comment(st.issue)
    payload = OUT / ".status_body.json"
    payload.write_text(json.dumps({"body": body}), encoding="utf-8")
    if cid:
        gh(["api", "-X", "PATCH", f"repos/{REPO}/issues/comments/{cid}", "--input", str(payload)],
           check=False)
    else:
        gh(["api", "-X", "POST", f"repos/{REPO}/issues/{st.issue}/comments", "--input", str(payload)],
           check=False)


def comment(issue: int, body: str) -> None:
    p = OUT / ".comment.json"
    p.write_text(json.dumps({"body": redact(body)}), encoding="utf-8")
    gh(["api", "-X", "POST", f"repos/{REPO}/issues/{issue}/comments", "--input", str(p)], check=False)


TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def maintainer_clarifications(comments: list[dict[str, Any]]) -> str:
    """Comments by maintainers become part of the issue (ADR-010/012). Comments from
    anyone else are left out, so a stranger cannot add instructions to the agents' input."""
    keep = []
    for c in comments:
        body = (c.get("body") or "").strip()
        if (c.get("author_association") in TRUSTED_ASSOCIATIONS
                and (c.get("user") or {}).get("type") != "Bot"
                and body and not body.startswith("/autose") and STATUS_MARK not in body):
            keep.append(f"- @{c['user']['login']}: {body}")
    return ("\n\n---\nClarifications from maintainers (oldest first):\n" + "\n".join(keep)) if keep else ""


def fetch_issue(issue: int) -> dict[str, Any]:
    data = json.loads(gh(["issue", "view", str(issue), "--json", "title,body,author,number"]))
    raw = gh(["api", "--paginate", f"repos/{REPO}/issues/{issue}/comments"], check=False) or "[]"
    try:
        comments = json.loads(raw.replace("][", ","))
    except json.JSONDecodeError:
        comments = []
    data["body"] = (data.get("body") or "") + maintainer_clarifications(comments)
    return data
