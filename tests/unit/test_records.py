import pytest

from orisun_iwadi.domain.dataset import Dataset
from orisun_iwadi.domain.records import prepare_rows, raw_object_key, sha256, snapshot_bytes

DS = Dataset(name="t", description="", source_url="",
             columns=(("id", "integer"), ("v", "text")), primary_key=("id",))


def test_snapshot_hash_ignores_record_and_key_order():
    a = [{"id": 1, "v": "x"}, {"v": "y", "id": 2}]
    b = [{"id": 2, "v": "y"}, {"v": "x", "id": 1}]
    assert sha256(snapshot_bytes(a)) == sha256(snapshot_bytes(b))


def test_snapshot_hash_changes_when_content_changes():
    assert sha256(snapshot_bytes([{"id": 1, "v": "x"}])) != sha256(snapshot_bytes([{"id": 1, "v": "z"}]))


def test_raw_key_is_content_addressed():
    assert raw_object_key("gdc", "abc") == "raw/gdc/sha256=abc.jsonl.gz"


def test_prepare_rows_quarantines_bad_rows_and_dedupes():
    out = prepare_rows(DS, [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}, {"id": None, "v": "no key"},
                            {"id": "abc", "v": "bad type"}])
    assert [q.reasons for q in out.quarantined] == [["id is required"],
                                                    ["id='abc' is not a valid integer"]]
    assert out.duplicates == 1
    assert [r["v"] for r in out.rows] == ["b"]  # last one wins
    assert len(out.rows[0]["_record_hash"]) == 64


def test_dataset_rejects_primary_key_not_in_columns():
    with pytest.raises(ValueError):
        Dataset(name="bad", description="", source_url="", columns=(("a", "text"),),
                primary_key=("id",))
