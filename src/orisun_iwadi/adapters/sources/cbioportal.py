"""Cancer genomics study catalog from cBioPortal."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ...domain.dataset import Dataset
from ...domain.quality import between, not_null
from .base import ApiSource
from .http import TIMEOUT, make_session

URL = "https://www.cbioportal.org/api/studies"


def fetch(max_records: int | None = None) -> Iterator[dict[str, Any]]:
    resp = make_session().get(
        URL, timeout=TIMEOUT,
        params={"projection": "SUMMARY", "pageSize": 100000, "sortBy": "studyId"},
    )
    resp.raise_for_status()
    studies = resp.json()
    yield from studies[:max_records] if max_records else studies


def transform(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "study_id": r.get("studyId"),
        "name": r.get("name"),
        "description": r.get("description"),
        "cancer_type_id": r.get("cancerTypeId"),
        "public_study": r.get("publicStudy"),
        "pmid": r.get("pmid"),
        "citation": r.get("citation"),
        "reference_genome": r.get("referenceGenome"),
        "all_sample_count": r.get("allSampleCount"),
        "import_date": r.get("importDate"),
    }


DATASET = Dataset(
    name="cbioportal_studies",
    description="Catalog of public cancer genomics studies hosted on cBioPortal.",
    source_url=URL,
    columns=(
        ("study_id", "text"), ("name", "text"), ("description", "text"),
        ("cancer_type_id", "text"), ("public_study", "boolean"), ("pmid", "text"),
        ("citation", "text"), ("reference_genome", "text"), ("all_sample_count", "integer"),
        ("import_date", "timestamp"),
    ),
    primary_key=("study_id",),
    rules=(
        not_null("name"),
        between("all_sample_count", 0),
    ),
)

SOURCE = ApiSource(dataset=DATASET, fetch=fetch, to_row=transform)
