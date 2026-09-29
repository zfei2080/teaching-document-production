"""Tests for automatic-validator registration and safe isolation."""

import unittest

from verification_registry import VerificationRegistry, VerificationResult


class VerificationRegistryTests(unittest.TestCase):
    def test_unknown_validator_is_unsupported_not_approved(self):
        registry = VerificationRegistry()
        result = registry.verify("missing", {"id": "q1"})
        self.assertEqual(result.status, "unsupported")
        self.assertIsNone(result.computed_answer)

    def test_validator_returns_independent_evidence(self):
        registry = VerificationRegistry()
        registry.register(
            "demo",
            lambda question: VerificationResult(
                status="pass",
                validator_id="demo",
                computed_answer="4",
                evidence="独立计算 2+2=4",
            ),
        )
        result = registry.verify("demo", {"stem": "2+2"})
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.computed_answer, "4")
        self.assertIn("独立计算", result.evidence)

    def test_duplicate_validator_id_is_rejected(self):
        registry = VerificationRegistry()
        registry.register("demo", lambda question: VerificationResult("pass", "demo", "evidence"))
        with self.assertRaises(ValueError):
            registry.register("demo", lambda question: VerificationResult("pass", "demo", "evidence"))


if __name__ == "__main__":
    unittest.main()
