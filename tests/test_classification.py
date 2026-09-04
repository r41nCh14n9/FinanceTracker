from unittest.mock import MagicMock

from src.classification import (
    ClassificationService,
    build_classification_tags,
    display_industry,
    group_by_first_concept,
    invert_category_table,
)
from src.fetcher import FinMindClient
from src.storage import SnapshotRepository


def test_invert_category_table_builds_stock_to_categories_lookup():
    table = {
        "半導體業": {"members": [{"stock_id": "2330", "stock_name": "台積電"}], "updated_at": "t1"},
        "航運業": {"members": [{"stock_id": "2603", "stock_name": "長榮"}], "updated_at": "t2"},
    }

    reverse = invert_category_table(table)

    assert reverse == {"2330": ["半導體業"], "2603": ["航運業"]}


def test_invert_category_table_collects_multiple_categories_for_same_stock():
    """一檔股票可能同時列在多個分類底下（概念標籤情境），要全部依序收進同一個陣列。"""
    table = {
        "IC 製造": {"members": [{"stock_id": "2330", "stock_name": "台積電"}]},
        "先進封裝": {"members": [{"stock_id": "2330", "stock_name": "台積電"}]},
    }

    reverse = invert_category_table(table)

    assert reverse == {"2330": ["IC 製造", "先進封裝"]}


def test_invert_category_table_handles_empty_table():
    assert invert_category_table({}) == {}


def test_group_by_first_concept_groups_in_first_seen_order():
    items = ["2330", "3661", "2603"]
    concept_map = {"2330": ["半導體"], "3661": ["半導體"], "2603": ["航運"]}

    grouped = group_by_first_concept(items, lambda s: s, concept_map)

    assert grouped == [("半導體", ["2330", "3661"]), ("航運", ["2603"])]


def test_group_by_first_concept_uses_only_first_concept_when_multiple_match():
    """一檔股票同時符合多個概念（如「電源」與「被動」）時，分組只取第一個，
    避免同一檔股票在報告中重複出現好幾次；呈現時要不要列出全部概念是呼叫端的事，
    這裡只負責分組。
    """
    concept_map = {"2308": ["電源", "被動"]}

    grouped = group_by_first_concept(["2308"], lambda s: s, concept_map)

    assert grouped == [("電源", ["2308"])]


def test_group_by_first_concept_puts_unclassified_bucket_last():
    """查無概念的項目一律歸進 None 那組，且固定排在最後，即使它在原始清單中最早出現。"""
    items = ["9999", "2330"]
    concept_map = {"2330": ["半導體"]}

    grouped = group_by_first_concept(items, lambda s: s, concept_map)

    assert grouped == [("半導體", ["2330"]), (None, ["9999"])]


def test_group_by_first_concept_handles_empty_items():
    assert group_by_first_concept([], lambda s: s, {}) == []


def test_display_industry_strips_trailing_industry_suffix():
    assert display_industry("半導體業") == "半導體"


def test_display_industry_leaves_names_without_suffix_unchanged():
    assert display_industry("電腦及週邊設備") == "電腦及週邊設備"


def test_build_classification_tags_orders_industry_tier_then_concepts():
    industry_map = {"2330": "半導體業"}
    concept_map = {"2330": ["IC 製造", "先進封裝"]}

    tags = build_classification_tags("大型", "2330", industry_map, concept_map)

    assert tags == ["半導體", "大型", "IC 製造", "先進封裝"]


def test_build_classification_tags_returns_empty_list_when_nothing_available():
    assert build_classification_tags(None, "9999", {}, {}) == []


def _make_repo(tmp_path):
    return SnapshotRepository(data_dir=tmp_path / "data")


def test_ensure_industry_categories_skips_finmind_when_already_known(tmp_path):
    storage = _make_repo(tmp_path)
    storage.write_industry_tags({"半導體業": {"members": [{"stock_id": "2330", "stock_name": "台積電"}], "updated_at": "t1"}})
    finmind = MagicMock(spec=FinMindClient)

    result = ClassificationService(finmind, storage).ensure_industry_categories(["2330"])

    assert result == {"2330": "半導體業"}
    finmind.fetch_stock_industry.assert_not_called()


def test_ensure_industry_categories_queries_finmind_and_persists_new_stock(tmp_path):
    storage = _make_repo(tmp_path)
    finmind = MagicMock(spec=FinMindClient)
    finmind.fetch_stock_industry.return_value = {
        "stock_id": "3661", "stock_name": "世芯-KY", "industry_category": "半導體業",
    }

    result = ClassificationService(finmind, storage).ensure_industry_categories(["3661"])

    assert result == {"3661": "半導體業"}
    saved = storage.read_industry_tags()
    assert saved["半導體業"]["members"] == [{"stock_id": "3661", "stock_name": "世芯-KY"}]
    finmind.fetch_stock_industry.assert_called_once_with("3661")


def test_ensure_industry_categories_appends_to_existing_industry(tmp_path):
    storage = _make_repo(tmp_path)
    storage.write_industry_tags({"半導體業": {"members": [{"stock_id": "2330", "stock_name": "台積電"}], "updated_at": "t1"}})
    finmind = MagicMock(spec=FinMindClient)
    finmind.fetch_stock_industry.return_value = {
        "stock_id": "3661", "stock_name": "世芯-KY", "industry_category": "半導體業",
    }

    ClassificationService(finmind, storage).ensure_industry_categories(["2330", "3661"])

    saved = storage.read_industry_tags()
    assert [m["stock_id"] for m in saved["半導體業"]["members"]] == ["2330", "3661"]


def test_ensure_industry_categories_skips_stock_when_finmind_raises(tmp_path):
    """查詢失敗不能拖累其他股票，也不該在本地表留下負面紀錄——下次還要有機會重試。"""
    storage = _make_repo(tmp_path)
    finmind = MagicMock(spec=FinMindClient)
    finmind.fetch_stock_industry.side_effect = RuntimeError("boom")

    result = ClassificationService(finmind, storage).ensure_industry_categories(["3661"])

    assert result == {}
    assert storage.read_industry_tags() == {}


def test_ensure_industry_categories_skips_stock_when_finmind_returns_no_category(tmp_path):
    storage = _make_repo(tmp_path)
    finmind = MagicMock(spec=FinMindClient)
    finmind.fetch_stock_industry.return_value = {"stock_id": "0050", "stock_name": "元大台灣50", "industry_category": ""}

    result = ClassificationService(finmind, storage).ensure_industry_categories(["0050"])

    assert result == {}
    assert storage.read_industry_tags() == {}


def test_ensure_industry_categories_does_not_write_when_nothing_changed(tmp_path):
    storage = _make_repo(tmp_path)
    finmind = MagicMock(spec=FinMindClient)

    result = ClassificationService(finmind, storage).ensure_industry_categories([])

    assert result == {}
    assert storage.read_industry_tags() == {}
