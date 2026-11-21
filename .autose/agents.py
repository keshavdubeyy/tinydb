"""Runs one agent session with the Claude Agent SDK, with per-agent tool policy (ADR-005),
model (ADR-006) and budget/time limits (ADR-009)."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from common import redact

# ---- Models (ADR-006) ------------------------------------------------------
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"

SAFE_PYTEST = re.compile(r"^python3? -m pytest(\s+[\w./:=\-\[\]]+)*\s*$")


@dataclass
class Policy:
    """What one agent may touch. Checked on every tool call by a PreToolUse hook."""
    tools: list[str]
    read_roots: list[Path]
    write_allowed: Callable[[Path], bool] = lambda p: False
    bash_allowed: bool = False


@dataclass
class AgentResult:
    text: str = ""
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    turns: int = 0
    ok: bool = True
    error: str = ""
    denials: list[str] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)


def _inside(path: Path, roots: list[Path]) -> bool:
    try:
        rp = path.resolve()
    except OSError:
        return False
    if "/.git/" in str(rp) + "/" and not str(rp).endswith("/.git"):
        return False
    return any(rp == r or r in rp.parents for r in roots)


def check_tool(policy: Policy, cwd: Path, tool: str, inp: dict) -> str | None:
    """Return a denial reason, or None if the call is allowed."""
    if tool not in policy.tools:
        return f"tool {tool} is not available to this agent"
    if tool in ("Read", "Grep", "Glob"):
        raw = inp.get("file_path") or inp.get("path") or str(cwd)
        p = (cwd / raw) if not Path(raw).is_absolute() else Path(raw)
        if not _inside(p, policy.read_roots) or ".git" in Path(raw).parts:
            return f"reading {raw} is outside this agent's allowed area"
        return None
    if tool in ("Edit", "Write", "MultiEdit"):
        raw = inp.get("file_path", "")
        p = (cwd / raw) if not Path(raw).is_absolute() else Path(raw)
        try:
            rel = p.resolve().relative_to(cwd.resolve())
        except ValueError:
            return f"writing {raw} is outside the repository"
        if not policy.write_allowed(rel):
            return f"this agent may not write {rel}"
        return None
    if tool == "Bash":
        cmd = (inp.get("command") or "").strip()
        if not policy.bash_allowed or not SAFE_PYTEST.match(cmd):
            return "only `python -m pytest [args]` is allowed"
        return None
    return None


async def _run(system: str, user: str, model: str, policy: Policy, cwd: Path,
               max_turns: int, budget_usd: float, effort: str, timeout_s: int,
               followups: list[Callable[[str], str | None]] | None = None) -> AgentResult:
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
                                  HookMatcher, ResultMessage, TextBlock, ToolUseBlock)

    res = AgentResult()

    async def guard(input_data, tool_use_id, context):
        reason = check_tool(policy, cwd, input_data.get("tool_name", ""),
                            input_data.get("tool_input", {}) or {})
        if reason:
            res.denials.append(f"{input_data.get('tool_name')}: {reason}")
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
        return {}

    opts = ClaudeAgentOptions(
        system_prompt=system,
        model=model,
        tools=list(policy.tools),
        allowed_tools=list(policy.tools),
        disallowed_tools=["WebFetch", "WebSearch", "Task", "Agent"],
        permission_mode="dontAsk",
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[guard])]},
        max_turns=max_turns,
        max_budget_usd=max(budget_usd, 0.01),
        effort=effort,
        cwd=str(cwd),
        setting_sources=[],
    )

    async def converse() -> None:
        async with ClaudeSDKClient(options=opts) as client:
            pending: str | None = user
            fu = list(followups or [])
            while pending is not None:
                await client.query(pending)
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for b in msg.content:
                            if isinstance(b, TextBlock):
                                res.transcript.append(f"[text] {b.text}")
                            elif isinstance(b, ToolUseBlock):
                                res.transcript.append(f"[tool] {b.name} {str(b.input)[:300]}")
                    elif isinstance(msg, ResultMessage):
                        res.text = msg.result or res.text
                        res.cost_usd += msg.total_cost_usd or 0.0
                        res.turns += msg.num_turns or 0
                        u = msg.usage or {}
                        res.tokens_in += int(u.get("input_tokens", 0)) + \
                            int(u.get("cache_read_input_tokens", 0)) + \
                            int(u.get("cache_creation_input_tokens", 0))
                        res.tokens_out += int(u.get("output_tokens", 0))
                        if msg.is_error:
                            res.ok = False
                            res.error = f"{msg.subtype}: {msg.errors or ''}"
                pending = None
                while fu and pending is None:
                    pending = fu.pop(0)(res.text)

    try:
        await asyncio.wait_for(converse(), timeout=timeout_s)
    except asyncio.TimeoutError:
        res.ok = False
        res.error = f"timeout after {timeout_s}s"
    except Exception as exc:  # noqa: BLE001 - report any SDK failure as a failed stage
        res.ok = False
        res.error = f"{type(exc).__name__}: {exc}"
    res.text = redact(res.text or "")
    res.transcript = [redact(t) for t in res.transcript]
    return res


def run_agent(system: str, user: str, *, model: str, policy: Policy, cwd: Path,
              max_turns: int, budget_usd: float, effort: str, timeout_s: int,
              followups: list[Callable[[str], str | None]] | None = None) -> AgentResult:
    return asyncio.run(_run(system, user, model, policy, cwd, max_turns, budget_usd,
                            effort, timeout_s, followups))
