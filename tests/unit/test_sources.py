"""Each source maps a raw API record to a flat row with exactly its dataset's columns."""

from orisun_iwadi.adapters.sources import ALL_SOURCES, cbioportal, cdc, civic, clinicaltrials, gdc


def test_every_source_outputs_exactly_its_columns():
    samples = {
        gdc.DATASET.name: {"case_id": "6e3b6b72-142d-4b8d-a462-28a205796e41"},
        cbioportal.DATASET.name: {"studyId": "acc_tcga"},
        clinicaltrials.DATASET.name: {"protocolSection": {"identificationModule": {"nctId": "NCT1"}}},
        cdc.DATASET.name: {"yearstart": "2015"},
        civic.DATASET.name: {"id": 1},
    }
    for src in ALL_SOURCES:
        row = src.to_row(samples[src.dataset.name])
        assert set(row) == set(src.dataset.column_names), src.dataset.name


def test_gdc_picks_primary_diagnosis():
    row = gdc.transform({
        "case_id": "x",
        "demographic": {"sex_at_birth": "female"},
        "diagnoses": [
            {"primary_diagnosis": "Other", "diagnosis_is_primary_disease": False},
            {"primary_diagnosis": "Adenocarcinoma, NOS", "diagnosis_is_primary_disease": True,
             "ajcc_pathologic_stage": "Stage IA"},
        ],
    })
    assert row["primary_diagnosis"] == "Adenocarcinoma, NOS"
    assert row["ajcc_pathologic_stage"] == "Stage IA"
    assert row["sex_at_birth"] == "female"


def test_cdc_casts_numbers_and_tolerates_blanks():
    row = cdc.transform({"yearstart": "2015", "datavalue": "21053.0", "lowconfidencelimit": ""})
    assert row["year_start"] == 2015
    assert row["data_value"] == 21053.0
    assert row["low_confidence_limit"] is None


def test_civic_therapies_sorted_for_stable_hash():
    row = civic.transform({"id": 1, "therapies": [{"name": "B"}, {"name": "A"}]})
    assert row["therapies"] == ["A", "B"]
