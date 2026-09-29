"""Registry for deterministic, independent question validators.

A validator receives a structured question and must either return a proof-like
verification result or explicitly decline. Declining is safe: the caller keeps
the question isolated. Validators never infer an answer from the stored answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping


@dataclass(frozen=True)
class VerificationResult:
    status: str  # pass | fail | unsupported
    validator_id: str
    evidence: str
    computed_answer: str | None = None


QuestionData = Mapping[str, object]
Validator = Callable[[QuestionData], VerificationResult]


class VerificationRegistry:
    def __init__(self) -> None:
        self._validators: dict[str, Validator] = {}

    def register(self, validator_id: str, validator: Validator) -> None:
        if validator_id in self._validators:
            raise ValueError(f"validator already registered: {validator_id}")
        self._validators[validator_id] = validator

    def verify(self, validator_id: str, question: QuestionData) -> VerificationResult:
        validator = self._validators.get(validator_id)
        if validator is None:
            return VerificationResult(
                status="unsupported",
                validator_id=validator_id,
                evidence="未找到对应自动验证器，题目保持隔离。",
            )
        return validator(question)

    @property
    def validator_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._validators))
