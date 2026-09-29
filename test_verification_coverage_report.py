import unittest

from verification_coverage_report import build_verification_coverage_report


class VerificationCoverageReportTestCase(unittest.TestCase):
    def test_build_verification_coverage_report_handles_multi_source_and_aggregation(self) -> None:
        report = build_verification_coverage_report(
            [
                {
                    "source_document_id": "doc-1",
                    "file_type": "pdf",
                    "validator": "ocr",
                    "evidence": {"coverage": "full", "status": "pass"},
                },
                {
                    "source_document_id": "doc-1",
                    "file_type": "pdf",
                    "validator": "ocr",
                    "evidence": {"coverage": "partial", "status": "fail"},
                },
                {
                    "source_document_id": "doc-2",
                    "file_type": "docx",
                    "validator": "hash-check",
                    "evidence": {"coverage": "none", "status": "fail"},
                },
                {
                    "source_document_id": "doc-2",
                    "file_type": "txt",
                    "validator": "hash-check",
                    "unsupported_reason": "plain_text_not_supported",
                    "evidence": {"coverage": "full", "status": "pass"},
                },
            ]
        )

        self.assertEqual(
            report["totals"],
            {
                "total": 4,
                "coverage": {"full": 2, "partial": 1, "none": 1, "unknown": 0, "invalid": 0},
                "verdict": {"pass": 1, "fail": 2, "unsupported": 1},
                "unsupported_reason": {"plain_text_not_supported": 1},
            },
        )
        self.assertEqual(
            report["by_source_document_id"]["doc-1"],
            {
                "total": 2,
                "coverage": {"full": 1, "partial": 1, "none": 0, "unknown": 0, "invalid": 0},
                "verdict": {"pass": 1, "fail": 1, "unsupported": 0},
                "unsupported_reason": {},
            },
        )
        self.assertEqual(report["by_file_type"]["pdf"]["coverage"]["partial"], 1)
        self.assertEqual(report["by_file_type"]["txt"]["verdict"]["unsupported"], 1)
        self.assertEqual(
            report["by_validator"]["hash-check"],
            {
                "total": 2,
                "coverage": {"full": 1, "partial": 0, "none": 1, "unknown": 0, "invalid": 0},
                "verdict": {"pass": 0, "fail": 1, "unsupported": 1},
                "unsupported_reason": {"plain_text_not_supported": 1},
            },
        )

    def test_build_verification_coverage_report_marks_missing_evidence_as_unknown_without_masking(self) -> None:
        report = build_verification_coverage_report(
            [
                {
                    "source_document_id": "doc-3",
                    "file_type": "pdf",
                    "validator": "ocr",
                    "evidence": None,
                }
            ]
        )

        self.assertEqual(
            report["totals"]["coverage"],
            {
                "full": 0,
                "partial": 0,
                "none": 0,
                "unknown": 1,
                "invalid": 0,
            },
        )
        self.assertEqual(report["totals"]["verdict"], {"pass": 0, "fail": 1, "unsupported": 0})
        self.assertEqual(report["by_source_document_id"]["doc-3"]["coverage"]["unknown"], 1)

    def test_build_verification_coverage_report_marks_hash_mismatch_as_invalid(self) -> None:
        report = build_verification_coverage_report(
            [
                {
                    "source_document_id": "doc-4",
                    "file_type": "pdf",
                    "validator": "hash-check",
                    "evidence": {
                        "coverage": "full",
                        "status": "pass",
                        "expected_hash": "abc",
                        "observed_hash": "xyz",
                    },
                }
            ]
        )

        self.assertEqual(
            report["totals"],
            {
                "total": 1,
                "coverage": {"full": 0, "partial": 0, "none": 0, "unknown": 0, "invalid": 1},
                "verdict": {"pass": 0, "fail": 1, "unsupported": 0},
                "unsupported_reason": {},
            },
        )

    def test_build_verification_coverage_report_keeps_unknown_file_type_unsupported(self) -> None:
        report = build_verification_coverage_report(
            [
                {
                    "source_document_id": "doc-5",
                    "file_type": "xls",
                    "validator": "tabular",
                    "unsupported_reason": "spreadsheet_not_supported",
                    "evidence": None,
                }
            ]
        )

        self.assertEqual(
            report["totals"]["coverage"],
            {
                "full": 0,
                "partial": 0,
                "none": 0,
                "unknown": 1,
                "invalid": 0,
            },
        )
        self.assertEqual(report["totals"]["verdict"], {"pass": 0, "fail": 0, "unsupported": 1})
        self.assertEqual(report["totals"]["unsupported_reason"], {"spreadsheet_not_supported": 1})


if __name__ == "__main__":
    unittest.main()
