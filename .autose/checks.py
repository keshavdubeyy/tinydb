"""Deterministic stage checks (ADR-007). Each returns (passed, reason/details)."""
from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

AC_RE = re.compile(r"\*\*AC-(\d+)\*\*[^:\n]*:\s*(.+)")
GWT_RE = re.compile(r"\bgiven\b.*\bwhen\b.*\bthen\b", re.I | re.S)
DESIGN_LINE_RE = re.compile(r"^\s*-\s*`([^`]+)`\s*::\s*`([^`]+)`(.*)$", re.M)
PROTECTED_PREFIXES = (".github/", ".autose/", "tests/")
PROTECTED_FILES = ("ARCHITECTURE.md", "pyproject.toml", "pytest.ini", "mypy.ini", "uv.lock")


def section(md: str, title: str) -> str:
    m = re.search(rf"^##\s+{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)", md, re.S | re.M | re.I)
    return m.group(1).strip() if m else ""


def acceptance_criteria(spec: str) -> list[tuple[str, str]]:
    return [(f"AC-{n}", text.strip()) for n, text in AC_RE.findall(section(spec, "Acceptance criteria"))]


def check_spec_structure(spec: str) -> tuple[bool, str]:
    problems = []
    if len(section(spec, "Goal")) < 15:
        problems.append("missing or empty '## Goal' section")
    acs = acceptance_criteria(spec)
    if not acs:
        problems.append("no acceptance criteria in the form '- **AC-1** ...: Given ..., when ..., then ...'")
    if len(acs) > 8:
        problems.append(f"too many acceptance criteria ({len(acs)}); keep 2-5")
    for ac_id, text in acs:
        if not GWT_RE.search(text):
            problems.append(f"{ac_id} is not in Given/When/Then form")
    ids = [a for a, _ in acs]
    if len(ids) != len(set(ids)):
        problems.append("duplicate AC ids")
    return (not problems, "; ".join(problems) or f"{len(acs)} criteria, structure ok")


def check_design(design: str, repo: Path) -> tuple[bool, str, list[str]]:
    entries = DESIGN_LINE_RE.findall(section(design, "Files and functions to change"))
    if not entries:
        return False, "no lines of the form - `path` :: `name` under 'Files and functions to change'", []
    problems, files = [], []
    for path, name, rest in entries:
        path = path.strip()
        is_new = "(NEW)" in rest.upper() or "(NEW)" in name.upper()
        if path.startswith(PROTECTED_PREFIXES) or path in PROTECTED_FILES:
            problems.append(f"{path} is not allowed to change (tests/config/pipeline files)")
            continue
        f = repo / path
        files.append(path)
        if not f.exists():
            if not is_new:
                problems.append(f"{path} does not exist (mark new files/functions with (NEW))")
            continue
        if is_new:
            continue
        leaf = name.replace("()", "").split(".")[-1].strip()
        src = f.read_text(encoding="utf-8", errors="replace")
        if not re.search(rf"^\s*(async\s+)?(def|class)\s+{re.escape(leaf)}\b", src, re.M):
            problems.append(f"`{name}` not found in {path}")
    return (not problems, "; ".join(problems) or f"{len(entries)} targets verified", files)


def changed_files(repo: Path) -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo,
                         capture_output=True, text=True).stdout
    files = []
    for line in out.splitlines():
        p = line[3:].strip()
        if " -> " in p:
            p = p.split(" -> ")[1]
        files.append(p.strip('"'))
    return files


def revert_files(repo: Path, files: list[str]) -> None:
    for f in files:
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", f], cwd=repo,
                                 capture_output=True).returncode == 0
        if tracked:
            subprocess.run(["git", "checkout", "--", f], cwd=repo, capture_output=True)
        else:
            (repo / f).unlink(missing_ok=True)


def implementer_violations(files: list[str]) -> list[str]:
    return [f for f in files if not f.startswith("tinydb/")]


def tester_violations(files: list[str], test_file: str) -> list[str]:
    return [f for f in files if f != test_file]


def check_build(repo: Path) -> tuple[bool, str]:
    for cmd in ([sys.executable, "-m", "compileall", "-q", "tinydb"],
                [sys.executable, "-c", "import tinydb, tinydb.table, tinydb.queries; print('ok')"]):
        p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            return False, (p.stdout + p.stderr)[-1500:]
    return True, "package compiles and imports"


def check_ac_tests(spec: str, test_src: str) -> tuple[bool, str]:
    ids = [a.split("-")[1] for a, _ in acceptance_criteria(spec)]
    found = set(re.findall(r"def\s+test_ac_(\d+)", test_src, re.I))
    missing = [f"AC-{i}" for i in ids if i not in found]
    return (not missing, f"missing tests for {', '.join(missing)}" if missing
            else f"every criterion has a test ({len(ids)})")


def run_tests(repo: Path, junit: Path, timeout: int = 300) -> tuple[bool, str, dict]:
    cmd = [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:cacheprovider",
           f"--junitxml={junit}"]
    p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=timeout)
    results: dict[str, str] = {}
    if junit.exists():
        for case in ET.parse(junit).getroot().iter("testcase"):
            name = case.get("name", "")
            status = "passed"
            if case.find("failure") is not None or case.find("error") is not None:
                status = "failed"
            elif case.find("skipped") is not None:
                status = "skipped"
            results[f"{case.get('classname', '')}::{name}"] = status
    tail = (p.stdout + p.stderr)[-3000:]
    return p.returncode == 0, tail, results
