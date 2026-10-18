from __future__ import annotations

import json
from pathlib import Path

from skillscout.models import Report


def test_checked_in_json_schema_matches_report_model():
    schema_path = Path(__file__).parents[1] / "docs" / "report.schema.json"
    checked_in = json.loads(schema_path.read_text(encoding="utf-8"))

    assert checked_in == Report.model_json_schema(by_alias=True)


def test_example_report_validates_against_public_report_model():
    example_path = Path(__file__).parents[1] / "docs" / "example-report.json"
    report = Report.model_validate_json(example_path.read_text(encoding="utf-8"))

    assert report.decision.value == "REVIEW"
    assert report.candidates[0].evidence.external_audit_status == "none"

