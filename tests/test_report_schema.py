from __future__ import annotations

import json
from pathlib import Path

from skillscout.models import Report


def test_checked_in_json_schema_matches_report_model():
    schema_path = Path(__file__).parents[1] / "docs" / "report.schema.json"
    checked_in = json.loads(schema_path.read_text(encoding="utf-8"))

    assert checked_in == Report.model_json_schema(by_alias=True)

