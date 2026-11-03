"""US state-level cancer indicators from the CDC Chronic Disease Indicators dataset."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ...domain.dataset import Dataset
from ...domain.quality import between, not_null, ordered
from .base import ApiSource
from .http import TIMEOUT, make_session

URL = "https://data.cdc.gov/resource/hksd-2xuw.json"
PAGE_SIZE = 50000


def fetch(max_records: int | None = None) -> Iterator[dict[str, Any]]:
    session = make_session()
    offset = count = 0
    while True:
        resp = session.get(URL, timeout=TIMEOUT, params={
            "$where": "topic='Cancer'", "$order": ":id",
            "$limit": PAGE_SIZE, "$offset": offset,
        })
        resp.raise_for_status()
        rows = resp.json()
        for row in rows:
            row.pop("geolocation", None)  # duplicate of location; not needed
            yield row
            count += 1
            if max_records and count >= max_records:
                return
        if len(rows) < PAGE_SIZE:
            return
        offset += PAGE_SIZE


def _int(v: Any) -> int | None:
    return int(v) if v not in (None, "") else None


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def transform(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "year_start": _int(r.get("yearstart")),
        "year_end": _int(r.get("yearend")),
        "location_id": r.get("locationid"),
        "location_abbr": r.get("locationabbr"),
        "location_desc": r.get("locationdesc"),
        "question_id": r.get("questionid"),
        "question": r.get("question"),
        "data_source": r.get("datasource"),
        "data_value_type_id": r.get("datavaluetypeid"),
        "data_value_type": r.get("datavaluetype"),
        "data_value_unit": r.get("datavalueunit"),
        "data_value": _num(r.get("datavalue")),
        "low_confidence_limit": _num(r.get("lowconfidencelimit")),
        "high_confidence_limit": _num(r.get("highconfidencelimit")),
        "stratification_category_id": r.get("stratificationcategoryid1"),
        "stratification_category": r.get("stratificationcategory1"),
        "stratification_id": r.get("stratificationid1"),
        "stratification": r.get("stratification1"),
        "footnote": r.get("datavaluefootnote"),
    }


DATASET = Dataset(
    name="cdc_cancer_indicators",
    description="State-level cancer incidence/mortality/screening indicators (CDC CDI).",
    source_url=URL,
    columns=(
        ("year_start", "integer"), ("year_end", "integer"), ("location_id", "text"),
        ("location_abbr", "text"), ("location_desc", "text"), ("question_id", "text"),
        ("question", "text"), ("data_source", "text"), ("data_value_type_id", "text"),
        ("data_value_type", "text"), ("data_value_unit", "text"), ("data_value", "numeric"),
        ("low_confidence_limit", "numeric"), ("high_confidence_limit", "numeric"),
        ("stratification_category_id", "text"), ("stratification_category", "text"),
        ("stratification_id", "text"), ("stratification", "text"), ("footnote", "text"),
    ),
    # The dataset has no single ID column; this combination is unique.
    primary_key=("year_start", "year_end", "location_id", "question_id",
                 "data_value_type_id", "stratification_id"),
    rules=(
        not_null("location_abbr"),
        between("year_start", 1990, 2100),
        ordered("year_start", "year_end"),
        between("data_value", 0),
        ordered("low_confidence_limit", "data_value", "high_confidence_limit"),
    ),
)

SOURCE = ApiSource(dataset=DATASET, fetch=fetch, to_row=transform)
