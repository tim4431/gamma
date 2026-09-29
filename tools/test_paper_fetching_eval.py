import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from paper_fetching_eval import (
    DEFAULT_SCENARIOS,
    InvalidReport,
    main,
    make_template,
    read_json,
    summarize,
    validate_results,
    validate_scenarios,
)


class PaperFetchingEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.cases = validate_scenarios(read_json(DEFAULT_SCENARIOS))
        self.report = make_template(self.cases)

    def assessed_report(self):
        report = copy.deepcopy(self.report)
        report["run_kind"] = "synthetic"
        report["outcomes"][0].update(
            discovery_relevant=True,
            correct_document=False,
            version_correct=True,
            human_intervention=False,
            extraction_usable=None,
            elapsed_seconds=2.5,
            requests=3,
            cost_usd=0.02,
            evidence=["Synthetic fixture: relevant result, wrong downloaded PDF."],
        )
        return report

    def test_unassessed_template_does_not_claim_success_or_zero_cost(self):
        summary = summarize(validate_results(self.report, self.cases), self.cases)
        self.assertEqual(summary["cases_with_assessments"], 0)
        self.assertIsNone(summary["assessments"]["correct_document"]["true_rate"])
        self.assertEqual(summary["assessments"]["correct_document"]["unassessed"], len(self.cases))
        self.assertIsNone(summary["measurements"]["cost_usd"]["total"])

    def test_partial_report_preserves_missing_cases_and_assessed_denominators(self):
        report = self.assessed_report()
        report["outcomes"] = report["outcomes"][:1]
        summary = summarize(validate_results(report, self.cases), self.cases)
        self.assertEqual(summary["cases_submitted"], 1)
        self.assertEqual(len(summary["missing_cases"]), len(self.cases) - 1)
        self.assertEqual(summary["assessments"]["discovery_relevant"]["true_rate"], 1.0)
        self.assertEqual(summary["assessments"]["correct_document"]["true_rate"], 0.0)
        self.assertEqual(summary["assessments"]["discovery_relevant"]["assessed"], 1)
        self.assertEqual(summary["assessments"]["human_intervention"]["false"], 1)
        self.assertEqual(summary["measurements"]["requests"], {
            "supplied": 1, "missing": len(self.cases) - 1, "total": 3,
        })

    def test_identity_failure_is_separate_from_version_handling(self):
        report = self.assessed_report()
        summary = summarize(validate_results(report, self.cases), self.cases)
        self.assertEqual(summary["assessments"]["version_correct"]["true"], 1)
        self.assertEqual(summary["assessments"]["correct_document"]["false"], 1)
        self.assertEqual(summary["assessments"]["extraction_usable"]["assessed"], 0)

    def test_duplicate_unknown_case_and_typo_are_rejected(self):
        mutations = (
            lambda report: report["outcomes"].append(copy.deepcopy(report["outcomes"][0])),
            lambda report: report["outcomes"][0].update(case_id="unknown"),
            lambda report: report["outcomes"][0].update(correct_documents=True),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                report = self.assessed_report()
                mutate(report)
                with self.assertRaises(InvalidReport):
                    validate_results(report, self.cases)

    def test_assessments_require_boolean_or_null_and_evidence(self):
        for value in (1, "true", [], {}):
            with self.subTest(value=value):
                report = self.assessed_report()
                report["outcomes"][0]["discovery_relevant"] = value
                with self.assertRaises(InvalidReport):
                    validate_results(report, self.cases)
        report = self.assessed_report()
        report["outcomes"][0]["evidence"] = []
        with self.assertRaisesRegex(InvalidReport, "supporting evidence"):
            validate_results(report, self.cases)

    def test_invalid_measurements_are_rejected(self):
        values = (
            ("requests", 1.5),
            ("requests", True),
            ("requests", -1),
            ("elapsed_seconds", float("nan")),
            ("elapsed_seconds", float("inf")),
            ("elapsed_seconds", -1),
            ("cost_usd", "0.1"),
            ("cost_usd", False),
        )
        for field, value in values:
            with self.subTest(field=field, value=value):
                report = self.assessed_report()
                report["outcomes"][0][field] = value
                with self.assertRaises(InvalidReport):
                    validate_results(report, self.cases)

    def test_invalid_scenario_and_report_versions_are_rejected(self):
        for version in (True, 2, "1"):
            with self.subTest(version=version):
                self.report["schema_version"] = version
                with self.assertRaises(InvalidReport):
                    validate_results(self.report, self.cases)
                with self.assertRaises(InvalidReport):
                    validate_scenarios({"schema_version": version, "cases": self.cases})

    def test_cli_accepts_bom_and_produces_json_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recorded.json"
            path.write_text(json.dumps(self.assessed_report()), encoding="utf-8-sig")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--results", str(path), "--json"])
        self.assertEqual(code, 0)
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["run_kind"], "synthetic")
        self.assertEqual(summary["measurements"]["cost_usd"]["total"], 0.02)

    def test_cli_rejects_invalid_report_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text("{", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["--results", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("Invalid evaluation input", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
