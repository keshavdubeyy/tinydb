# SRS Requirements Skill (condensed for a single GitHub issue)

Condensed from the author's A1 skill (`a1_skill_full.md`). Kept: the rules that decide whether
a spec is honest and testable. Dropped: stakeholder analysis, MCP research, NFR/risk/dependency
tables, multi-round clarification loops. One GitHub issue has no stakeholders to interview,
and those sections do not apply. What changed and why is listed at the end.

## Core rules (from A1 §1.1, §3.3)

1. Turn the request into requirements a reviewer can check. Never invent requirements.
2. Every item has exactly one grounding status:
   - **[CONFIRMED]**: stated directly in the issue.
   - **[INFERRED]**: your reasonable reading of the issue. Say why.
   - **[UNRESOLVED]**: essential information is missing or conflicting.
3. Never mark something [CONFIRMED] if any part of it depends on your own interpretation.
   Split the item, or mark it [INFERRED].

## Ambiguous words (A1 §2.5)

Words like "robust", "forgiving", "better", "fast", "friendly" don't describe measurable
behaviour. For each one:
- keep the original word, and say which behaviour it most likely refers to;
- do not silently turn your interpretation into a [CONFIRMED] requirement.

## Functional requirements (A1 §3.6.2)

- One identifiable behaviour per requirement: "The system shall …".
- Do not invent features the issue does not ask for.

## Assumptions (A1 §3.6.5)

- Every assumption is [INFERRED], says why you made it, and says how a maintainer could
  confirm it.
- Keep assumptions clearly separate from confirmed requirements.

## Acceptance criteria (A1 §3.6.8, §4.5.2, §4.6.3)

- Every criterion has an ID (`AC-1`, `AC-2`, …) and checks one requirement.
- Write it as **Given … , when … , then …** with an observable outcome (a return value, an
  exception type, a stored value).
- Name the concrete API involved (function, method, argument) so it can be tested.
- Do not invent thresholds, limits or behaviours the issue doesn't support.
- Testability check for each criterion: are the input condition and the expected outcome
  both defined? Could a unit test check it?
- Include at least one criterion that protects existing behaviour, when the change could
  break it.

## Pipeline-mode adaptation (changed from A1)

In A1, unresolved items wait for a stakeholder's answer. In this pipeline there is no one to
ask during the run, and the run must produce testable criteria. So:

- When the issue is vague, choose the **most reasonable interpretation** that a TinyDB
  maintainer would expect. Write the criteria for it, mark them **[INFERRED]**, and record
  the interpretation as an assumption with its reason.
- List the questions you would have asked under **Open questions**. They are shown to the
  human reviewer in the PR, who can reject the interpretation.
- Use [UNRESOLVED] only if no reasonable interpretation exists at all. The run will then
  stop and ask a human.
