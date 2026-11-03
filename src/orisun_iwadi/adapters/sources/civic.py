"""Clinical evidence linking cancer variants to diagnosis/prognosis/therapy, from CIViC."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ...domain.dataset import Dataset
from ...domain.quality import between, one_of
from .base import ApiSource
from .http import TIMEOUT, make_session

URL = "https://civicdb.org/api/graphql"
PAGE_SIZE = 100
QUERY = """
query Evidence($first: Int!, $after: String) {
  evidenceItems(first: $first, after: $after) {
    pageInfo { endCursor hasNextPage }
    nodes {
      id name status evidenceType evidenceLevel evidenceDirection significance
      evidenceRating description
      molecularProfile { id name }
      disease { id name doid }
      therapies { id name }
      source { citationId sourceType }
    }
  }
}
"""


def fetch(max_records: int | None = None) -> Iterator[dict[str, Any]]:
    session = make_session()
    after, count = None, 0
    while True:
        resp = session.post(URL, timeout=TIMEOUT, json={
            "query": QUERY, "variables": {"first": PAGE_SIZE, "after": after},
        })
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            raise RuntimeError(f"CIViC GraphQL error: {payload['errors']}")
        page = payload["data"]["evidenceItems"]
        for node in page["nodes"]:
            yield node
            count += 1
            if max_records and count >= max_records:
                return
        if not page["pageInfo"]["hasNextPage"]:
            return
        after = page["pageInfo"]["endCursor"]


def transform(r: dict[str, Any]) -> dict[str, Any]:
    mp = r.get("molecularProfile") or {}
    disease = r.get("disease") or {}
    source = r.get("source") or {}
    return {
        "evidence_id": r.get("id"),
        "name": r.get("name"),
        "status": r.get("status"),
        "evidence_type": r.get("evidenceType"),
        "evidence_level": r.get("evidenceLevel"),
        "evidence_direction": r.get("evidenceDirection"),
        "significance": r.get("significance"),
        "evidence_rating": r.get("evidenceRating"),
        "molecular_profile": mp.get("name"),
        "disease_name": disease.get("name"),
        "disease_doid": disease.get("doid"),
        "therapies": sorted(t["name"] for t in r.get("therapies") or [] if t.get("name")),
        "source_type": source.get("sourceType"),
        "source_citation_id": source.get("citationId"),
        "description": r.get("description"),
    }


DATASET = Dataset(
    name="civic_evidence",
    description="Curated clinical evidence for cancer variants from the CIViC knowledgebase.",
    source_url=URL,
    columns=(
        ("evidence_id", "integer"), ("name", "text"), ("status", "text"),
        ("evidence_type", "text"), ("evidence_level", "text"), ("evidence_direction", "text"),
        ("significance", "text"), ("evidence_rating", "integer"), ("molecular_profile", "text"),
        ("disease_name", "text"), ("disease_doid", "text"), ("therapies", "jsonb"),
        ("source_type", "text"), ("source_citation_id", "text"), ("description", "text"),
    ),
    primary_key=("evidence_id",),
    rules=(
        one_of("status", {"ACCEPTED", "SUBMITTED", "REJECTED"}),
        one_of("evidence_level", {"A", "B", "C", "D", "E"}),
        between("evidence_rating", 1, 5),
    ),
)

SOURCE = ApiSource(dataset=DATASET, fetch=fetch, to_row=transform)
