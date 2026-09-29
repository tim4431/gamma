"""Validate and summarize recorded paper-discovery outcomes without network calls."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any


DEFAULT_SCENARIOS = (
    Path(__file__).resolve().parents[1] / "docs/research/paper-fetching-cases.json"
)
ASSESSMENTS = (
    "discovery_relevant",
    "correct_document",
    "version_correct",
    "human_intervention",
    "extraction_usable",
)
MEASUREMENTS = ("elapsed_seconds", "requests", "cost_usd")


class InvalidReport(ValueError):
    """A scenario or outcome file cannot be evaluated reliably."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidReport(f"Cannot read {path}: {exc}") from exc


def validate_scenarios(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
        raise InvalidReport("Scenarios need integer schema_version 1.")
    if data["schema_version"] != 1 or not isinstance(data.get("cases"), list):
        raise InvalidReport("Scenarios need schema_version 1 and a cases array.")
    cases = data["cases"]
    if not cases:
        raise InvalidReport("Scenarios must contain at least one case.")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise InvalidReport("Each scenario must be an object.")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise InvalidReport(f"Missing or duplicate scenario id: {case_id!r}")
        seen.add(case_id)
        for field in ("kind", "prompt", "review"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise InvalidReport(f"{case_id}: {field} must be a nonempty string.")
        criteria = case.get("criteria")
        if not isinstance(criteria, list) or not criteria or any(
            not isinstance(item, str) or not item.strip() for item in criteria
        ):
            raise InvalidReport(f"{case_id}: criteria must be nonempty strings.")
    return cases


def make_template(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_label": "Replace with a descriptive run label",
        "run_kind": "recorded",
        "outcomes": [
            {
                "case_id": case["id"],
                **{field: None for field in ASSESSMENTS + MEASUREMENTS},
                "evidence": [],
                "notes": "",
            }
            for case in cases
        ],
    }


def validate_results(
    data: Any, cases: list[dict[str, Any]]
) -> dict[str, Any]:
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
    ):
        raise InvalidReport("Results need integer schema_version 1.")
    if not isinstance(data.get("run_label"), str) or not data["run_label"].strip():
        raise InvalidReport("Results need a nonempty run_label.")
    if data.get("run_kind") not in ("recorded", "synthetic"):
        raise InvalidReport("run_kind must be recorded or synthetic.")
    if not isinstance(data.get("outcomes"), list):
        raise InvalidReport("Results need an outcomes array.")
    known = {case["id"] for case in cases}
    seen: set[str] = set()
    allowed = {"case_id", "evidence", "notes", *ASSESSMENTS, *MEASUREMENTS}
    for outcome in data["outcomes"]:
        if not isinstance(outcome, dict):
            raise InvalidReport("Each outcome must be an object.")
        case_id = outcome.get("case_id")
        if not isinstance(case_id, str) or case_id not in known:
            raise InvalidReport(f"Unknown case_id: {case_id!r}")
        if case_id in seen:
            raise InvalidReport(f"Duplicate outcome for {case_id}.")
        seen.add(case_id)
        unexpected = set(outcome) - allowed
        if unexpected:
            raise InvalidReport(f"{case_id}: unknown fields: {', '.join(sorted(unexpected))}")
        for field in ASSESSMENTS:
            if field not in outcome or (
                outcome[field] is not None and type(outcome[field]) is not bool
            ):
                raise InvalidReport(f"{case_id}: {field} must be true, false or null.")
        for field in MEASUREMENTS:
            value = outcome.get(field)
            if value is None:
                continue
            expected_types = (int,) if field == "requests" else (int, float)
            if type(value) not in expected_types or value < 0:
                raise InvalidReport(f"{case_id}: {field} must be nonnegative numeric data.")
            if isinstance(value, float) and not math.isfinite(value):
                raise InvalidReport(f"{case_id}: {field} must be finite.")
        evidence = outcome.get("evidence")
        if not isinstance(evidence, list) or any(
            not isinstance(item, str) or not item.strip() for item in evidence
        ):
            raise InvalidReport(f"{case_id}: evidence must be an array of nonempty strings.")
        if any(outcome[field] is not None for field in ASSESSMENTS) and not evidence:
            raise InvalidReport(f"{case_id}: assessed outcomes require supporting evidence.")
        if "notes" in outcome and not isinstance(outcome["notes"], str):
            raise InvalidReport(f"{case_id}: notes must be a string.")
    return data


def summarize(data: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = {outcome["case_id"]: outcome for outcome in data["outcomes"]}
    summary: dict[str, Any] = {
        "schema_version": 1,
        "run_label": data["run_label"],
        "run_kind": data["run_kind"],
        "cases_expected": len(cases),
        "cases_submitted": len(outcomes),
        "cases_with_assessments": sum(
            any(outcome[field] is not None for field in ASSESSMENTS)
            for outcome in outcomes.values()
        ),
        "missing_cases": [case["id"] for case in cases if case["id"] not in outcomes],
        "assessments": {},
        "measurements": {},
        "outcomes": [
            outcomes.get(case["id"], {"case_id": case["id"], "missing": True})
            for case in cases
        ],
    }
    for field in ASSESSMENTS:
        assessed = [outcome[field] for outcome in outcomes.values() if outcome[field] is not None]
        true_count = sum(assessed)
        summary["assessments"][field] = {
            "true": true_count,
            "false": len(assessed) - true_count,
            "assessed": len(assessed),
            "unassessed": len(cases) - len(assessed),
            "true_rate": true_count / len(assessed) if assessed else None,
        }
    for field in MEASUREMENTS:
        supplied = [outcome[field] for outcome in outcomes.values() if outcome.get(field) is not None]
        summary["measurements"][field] = {
            "supplied": len(supplied),
            "missing": len(cases) - len(supplied),
            "total": sum(supplied) if supplied else None,
        }
    return summary


def render_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"{summary['run_kind'].capitalize()} outcome review: {summary['run_label']}",
        "No searches, downloads or model calls were run by this evaluator.",
        f"Cases submitted: {summary['cases_submitted']}/{summary['cases_expected']}; "
        f"with assessments: {summary['cases_with_assessments']}/{summary['cases_expected']}",
        "",
        "Assessment                True  False  Unassessed  True rate",
    ]
    for field, counts in summary["assessments"].items():
        rate = f"{counts['true_rate']:.1%}" if counts["true_rate"] is not None else "n/a"
        lines.append(
            f"{field:25} {counts['true']:4} {counts['false']:6} "
            f"{counts['unassessed']:11}  {rate}"
        )
    lines.extend(["", "Human intervention measures assistance used; its true rate is not a success rate."])
    for field, values in summary["measurements"].items():
        total = f"{values['total']:g}" if values["total"] is not None else "n/a"
        lines.append(f"{field}: {total} total from {values['supplied']} supplied case(s)")
    lines.extend(["", "Per-case assessments (T=true, F=false, -=unassessed):"])
    lines.append("Case                                Discovery Document Version Human Extraction")
    for outcome in summary["outcomes"]:
        if outcome.get("missing"):
            lines.append(f"{outcome['case_id']}: missing")
        else:
            flags = ["-" if outcome[field] is None else "T" if outcome[field] else "F" for field in ASSESSMENTS]
            lines.append(f"{outcome['case_id']:35} " + "        ".join(flags))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS, help="Scenario corpus JSON.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--template", action="store_true", help="Print an unassessed results template as JSON.")
    action.add_argument("--results", type=Path, help="Validate and summarize an existing results JSON file.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable scenarios or summary.")
    args = parser.parse_args(argv)
    try:
        cases = validate_scenarios(read_json(args.scenarios))
        if args.template:
            output: Any = make_template(cases)
        elif args.results:
            report = validate_results(read_json(args.results), cases)
            summary = summarize(report, cases)
            output = summary if args.json else render_summary(summary)
        else:
            output = {"schema_version": 1, "cases": cases} if args.json else "\n\n".join(
                f"{case['id']} ({case['kind']})\n{case['prompt']}\n"
                + "\n".join(f"- {criterion}" for criterion in case["criteria"])
                + f"\nReview: {case['review']}"
                for case in cases
            )
        print(output if isinstance(output, str) else json.dumps(output, indent=2, ensure_ascii=False))
        return 0
    except InvalidReport as exc:
        print(f"Invalid evaluation input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
