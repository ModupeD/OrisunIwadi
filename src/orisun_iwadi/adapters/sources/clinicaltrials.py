"""Currently recruiting cancer trials from ClinicalTrials.gov (API v2)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ...domain.dataset import Dataset
from ...domain.quality import between, matches, not_null, one_of
from .base import ApiSource
from .http import TIMEOUT, make_session

URL = "https://clinicaltrials.gov/api/v2/studies"
FIELDS = [
    "NCTId", "BriefTitle", "OverallStatus", "Phase", "StudyType", "Condition",
    "LeadSponsorName", "EnrollmentCount", "StartDate", "PrimaryCompletionDate",
    "LastUpdatePostDate",
]


def fetch(max_records: int | None = None) -> Iterator[dict[str, Any]]:
    session = make_session()
    params = {
        "query.cond": "cancer",
        "filter.overallStatus": "RECRUITING",
        "fields": ",".join(FIELDS),
        "pageSize": 1000,
    }
    count = 0
    while True:
        resp = session.get(URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        for study in data.get("studies", []):
            yield study
            count += 1
            if max_records and count >= max_records:
                return
        token = data.get("nextPageToken")
        if not token:
            return
        params["pageToken"] = token


def transform(r: dict[str, Any]) -> dict[str, Any]:
    p = r.get("protocolSection") or {}
    ident = p.get("identificationModule") or {}
    status = p.get("statusModule") or {}
    design = p.get("designModule") or {}
    return {
        "nct_id": ident.get("nctId"),
        "brief_title": ident.get("briefTitle"),
        "overall_status": status.get("overallStatus"),
        "study_type": design.get("studyType"),
        "phases": design.get("phases"),
        "conditions": (p.get("conditionsModule") or {}).get("conditions"),
        "lead_sponsor": ((p.get("sponsorCollaboratorsModule") or {})
                         .get("leadSponsor") or {}).get("name"),
        "enrollment_count": (design.get("enrollmentInfo") or {}).get("count"),
        # Dates can be partial ("2024-05"), so they stay text for now.
        "start_date": (status.get("startDateStruct") or {}).get("date"),
        "primary_completion_date": (status.get("primaryCompletionDateStruct") or {}).get("date"),
        "last_update_posted": (status.get("lastUpdatePostDateStruct") or {}).get("date"),
    }


DATASET = Dataset(
    name="clinical_trials_cancer",
    description="Recruiting interventional/observational cancer trials from ClinicalTrials.gov.",
    source_url=URL,
    columns=(
        ("nct_id", "text"), ("brief_title", "text"), ("overall_status", "text"),
        ("study_type", "text"), ("phases", "jsonb"), ("conditions", "jsonb"),
        ("lead_sponsor", "text"), ("enrollment_count", "integer"), ("start_date", "text"),
        ("primary_completion_date", "text"), ("last_update_posted", "text"),
    ),
    primary_key=("nct_id",),
    rules=(
        matches("nct_id", r"NCT\d{8}"),
        not_null("brief_title"),
        one_of("overall_status", {"RECRUITING"}),  # the API filter asks for these only
        between("enrollment_count", 0),
    ),
)

SOURCE = ApiSource(dataset=DATASET, fetch=fetch, to_row=transform)
