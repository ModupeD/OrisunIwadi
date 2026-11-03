"""TCGA patient cases from the NCI Genomic Data Commons (GDC) API."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ...domain.dataset import Dataset
from ...domain.quality import between, matches, one_of
from .base import ApiSource
from .http import TIMEOUT, make_session

URL = "https://api.gdc.cancer.gov/cases"
PAGE_SIZE = 1000
FIELDS = [
    "case_id", "submitter_id", "project.project_id", "primary_site", "disease_type",
    "updated_datetime",
    "demographic.sex_at_birth", "demographic.race", "demographic.ethnicity",
    "demographic.vital_status", "demographic.days_to_death",
    "diagnoses.diagnosis_is_primary_disease", "diagnoses.primary_diagnosis",
    "diagnoses.age_at_diagnosis", "diagnoses.ajcc_pathologic_stage",
    "diagnoses.tissue_or_organ_of_origin",
]
FILTERS = {"op": "=", "content": {"field": "project.program.name", "value": "TCGA"}}


def fetch(max_records: int | None = None) -> Iterator[dict[str, Any]]:
    session = make_session()
    offset = count = 0
    while True:
        resp = session.post(URL, timeout=TIMEOUT, json={
            "filters": FILTERS, "fields": ",".join(FIELDS),
            "size": PAGE_SIZE, "from": offset, "sort": "case_id:asc",
        })
        resp.raise_for_status()
        data = resp.json()["data"]
        for hit in data["hits"]:
            yield hit
            count += 1
            if max_records and count >= max_records:
                return
        offset += PAGE_SIZE
        if not data["hits"] or offset >= data["pagination"]["total"]:
            return


def transform(r: dict[str, Any]) -> dict[str, Any]:
    diagnoses = r.get("diagnoses") or []
    primary = next((d for d in diagnoses if d.get("diagnosis_is_primary_disease")),
                   diagnoses[0] if diagnoses else {})
    demo = r.get("demographic") or {}
    return {
        "case_id": r.get("case_id"),
        "submitter_id": r.get("submitter_id"),
        "project_id": (r.get("project") or {}).get("project_id"),
        "primary_site": r.get("primary_site"),
        "disease_type": r.get("disease_type"),
        "sex_at_birth": demo.get("sex_at_birth"),
        "race": demo.get("race"),
        "ethnicity": demo.get("ethnicity"),
        "vital_status": demo.get("vital_status"),
        "days_to_death": demo.get("days_to_death"),
        "primary_diagnosis": primary.get("primary_diagnosis"),
        "age_at_diagnosis_days": primary.get("age_at_diagnosis"),
        "ajcc_pathologic_stage": primary.get("ajcc_pathologic_stage"),
        "tissue_or_organ_of_origin": primary.get("tissue_or_organ_of_origin"),
        "diagnoses": diagnoses,
        "source_updated_at": r.get("updated_datetime"),
    }


DATASET = Dataset(
    name="gdc_tcga_cases",
    description="TCGA patient cases (demographics + primary diagnosis) from the NCI GDC API.",
    source_url=URL,
    columns=(
        ("case_id", "uuid"), ("submitter_id", "text"), ("project_id", "text"),
        ("primary_site", "text"), ("disease_type", "text"), ("sex_at_birth", "text"),
        ("race", "text"), ("ethnicity", "text"), ("vital_status", "text"),
        ("days_to_death", "numeric"), ("primary_diagnosis", "text"),
        ("age_at_diagnosis_days", "numeric"), ("ajcc_pathologic_stage", "text"),
        ("tissue_or_organ_of_origin", "text"), ("diagnoses", "jsonb"),
        ("source_updated_at", "timestamptz"),
    ),
    primary_key=("case_id",),
    rules=(
        matches("project_id", r"TCGA-[A-Z0-9]+"),
        one_of("vital_status", {"Alive", "Dead", "Not Reported", "Unknown"}),
        between("days_to_death", 0, 50_000),
        between("age_at_diagnosis_days", 0, 45_000),  # 0 to ~123 years
    ),
)

SOURCE = ApiSource(dataset=DATASET, fetch=fetch, to_row=transform)
