"""Unit tests for the rubric engine."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from procurement_decision_api.models import RubricCriterion
from procurement_decision_api.rubric import (
    compose_rationale,
    infer_status,
    weighted_score,
)


def _rc(id: str, result: str, weight: float | None = None) -> RubricCriterion:
    return RubricCriterion(id=id, result=result, weight=weight)  # type: ignore[arg-type]


class TestInferStatus:
    def test_empty_rubric_is_pending(self) -> None:
        assert infer_status([]) == "pending"

    def test_all_pass_is_approved(self) -> None:
        assert infer_status([_rc("a", "pass"), _rc("b", "pass")]) == "approved"

    def test_any_fail_is_rejected_with_remediation(self) -> None:
        rubric = [_rc("a", "pass"), _rc("b", "fail"), _rc("c", "partial")]
        assert infer_status(rubric) == "rejected-with-remediation"

    def test_partial_without_fail_is_approved_with_conditions(self) -> None:
        rubric = [_rc("a", "pass"), _rc("b", "partial")]
        assert infer_status(rubric) == "approved-with-conditions"

    def test_pass_with_condition_is_approved_with_conditions(self) -> None:
        rubric = [_rc("a", "pass"), _rc("b", "pass-with-condition")]
        assert infer_status(rubric) == "approved-with-conditions"

    def test_all_na_is_pending(self) -> None:
        rubric = [_rc("a", "n/a"), _rc("b", "n/a")]
        assert infer_status(rubric) == "pending"


class TestWeightedScore:
    def test_empty_returns_none(self) -> None:
        assert weighted_score([]) is None

    def test_all_pass_unit_weight(self) -> None:
        rubric = [_rc("a", "pass", 1.0), _rc("b", "pass", 1.0)]
        assert weighted_score(rubric) == 1.0

    def test_mixed(self) -> None:
        rubric = [
            _rc("a", "pass", 1.0),
            _rc("b", "fail", 1.0),
        ]
        assert weighted_score(rubric) == 0.5

    def test_weights_respected(self) -> None:
        rubric = [
            _rc("a", "pass", 0.2),  # 0.2 * 1.0 = 0.2
            _rc("b", "fail", 0.8),  # 0.8 * 0.0 = 0.0
        ]
        # weighted average = 0.2 / (0.2 + 0.8) = 0.2
        assert weighted_score(rubric) == pytest.approx(0.2)

    def test_weight_out_of_range_rejected_by_model(self) -> None:
        # Sanity-check the Pydantic constraint (weight must be in [0, 1]).
        with pytest.raises(ValidationError):
            _rc("a", "pass", 1.5)

    def test_na_excluded(self) -> None:
        rubric = [_rc("a", "pass", 1.0), _rc("b", "n/a", 1.0)]
        assert weighted_score(rubric) == 1.0

    def test_no_weights_treats_as_one(self) -> None:
        rubric = [_rc("a", "pass"), _rc("b", "fail")]
        # weights default to 1.0 in the formula
        assert weighted_score(rubric) == 0.5


class TestComposeRationale:
    def test_includes_vendor_and_status(self) -> None:
        rubric = [_rc("a", "pass"), _rc("b", "partial")]
        text = compose_rationale(
            rubric,
            status="approved-with-conditions",
            vendor_name="AcmeTutor Inc.",
            product_name="AcmeTutor 3.0",
            documents_count=3,
        )
        assert "AcmeTutor Inc." in text
        assert "AcmeTutor 3.0" in text
        assert "approved with conditions" in text
        assert "3 reviewed document" in text

    def test_lists_failing_criteria(self) -> None:
        rubric = [
            _rc("safety", "fail"),
            _rc("compliance", "pass"),
        ]
        text = compose_rationale(
            rubric,
            status="rejected-with-remediation",
            vendor_name="X",
            product_name=None,
            documents_count=1,
        )
        assert "safety" in text
        assert "Failing criteria" in text

    def test_no_failing_when_all_pass(self) -> None:
        rubric = [_rc("a", "pass"), _rc("b", "pass")]
        text = compose_rationale(
            rubric, status="approved", vendor_name="X", product_name=None, documents_count=0
        )
        assert "Failing criteria" not in text
