from orisun_iwadi.adapters.sources import ALL_SOURCES, cdc, civic, clinicaltrials
from orisun_iwadi.domain.quality import (
    between,
    failures,
    matches,
    not_null,
    one_of,
    ordered,
    type_rules,
)


def test_value_rules_pass_on_empty_values():
    rules = [one_of("s", {"A"}), between("n", 0, 5), matches("id", r"X\d"), ordered("lo", "hi")]
    assert failures(rules, {}) == []
    assert failures(rules, {"s": None, "n": "", "id": None}) == []


def test_value_rules_report_why_they_failed():
    row = {"s": "Z", "n": 9, "id": "Y1", "lo": 3, "hi": 1, "req": ""}
    rules = [one_of("s", {"A"}), between("n", 0, 5), matches("id", r"X\d"),
             ordered("lo", "hi"), not_null("req")]
    assert failures(rules, row) == [
        "s='Z' not in ['A']", "n=9 outside [0, 5]", "id='Y1' != /X\\d/", "lo=3.0 > hi=1.0",
        "req is required",
    ]


def test_type_rules_follow_postgres_types():
    rules = type_rules([("i", "integer"), ("n", "numeric"), ("u", "uuid"), ("b", "boolean"),
                        ("t", "timestamptz"), ("txt", "text")])
    good = {"i": "42", "n": "3.5", "u": "6e3b6b72-142d-4b8d-a462-28a205796e41", "b": True,
            "t": "2024-01-02T03:04:05Z", "txt": 123}
    assert failures(rules, good) == []
    bad = {"i": True, "n": "NaN", "u": "nope", "b": "yes", "t": "yesterday"}
    assert len(failures(rules, bad)) == 5


def test_dataset_rules_catch_bad_source_data():
    def reasons(src, raw):
        return failures(src.dataset.all_rules, src.to_row(raw))

    assert reasons(civic.SOURCE, {"id": 1, "evidenceRating": 7}) == [
        "evidence_rating=7 outside [1, 5]"]
    assert reasons(clinicaltrials.SOURCE, {"protocolSection": {
        "identificationModule": {"nctId": "BAD1", "briefTitle": "t"},
        "statusModule": {"overallStatus": "RECRUITING"}}}) == ["nct_id='BAD1' != /NCT\\d{8}/"]
    assert "year_start=2020.0 > year_end=2019.0" in reasons(cdc.SOURCE, {
        "yearstart": "2020", "yearend": "2019", "locationid": "1", "locationabbr": "AL",
        "questionid": "q", "datavaluetypeid": "t", "stratificationid1": "s"})


def test_every_dataset_has_business_rules():
    assert all(src.dataset.rules for src in ALL_SOURCES)
