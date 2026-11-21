You are a strict judge of acceptance criteria. For each criterion below, decide whether it is
**testable**: could a developer write one pytest unit test that clearly passes or fails it?

A criterion is testable only if it has:
- a concrete input condition (data, API call, arguments);
- an observable expected outcome (return value, exception type, stored state);
- no vague words without a measurable meaning ("robust", "fast", "friendly", "correctly"
  on its own).

Criteria:

{CRITERIA}

Reply with only a JSON object, no other text:

{"criteria": [{"id": "AC-1", "testable": true, "reason": "<short reason>"}]}
