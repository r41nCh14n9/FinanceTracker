"""維護本地的股票分類標籤（產業別／概念股），供每日通知組版時附加標籤與分組排序使用。

產業別由 FinMind 逐股查詢後累積寫入 data/reference/industry_tags.json；概念股標籤則是
維運人員手動維護的 config/concept_tags.json，程式只讀取、不寫入、也不呼叫任何外部來源。
兩份檔案採同一種「分類名稱 -> 成員清單」結構，因此共用同一套反查工具
invert_category_table()：產業別互斥，每檔股票只會落在一個分類底下；概念標籤則允許
同一檔股票同時出現在多個分類。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.fetcher import FinMindClient
from src.models import MarketCapTier
from src.storage import SnapshotRepository

logger = logging.getLogger(__name__)

TIER_LABELS = {
    MarketCapTier.LARGE: "大型",
    MarketCapTier.MID: "中型",
    MarketCapTier.SMALL: "中小型",
}

_INDUSTRY_SUFFIX = "業"


def display_industry(industry: str) -> str:
    """顯示層去掉官方產業別字尾的「業」字（如「半導體業」->「半導體」），純粹是
    為了讓標籤短一點；落地儲存的原始值不受影響。
    """
    return industry[: -len(_INDUSTRY_SUFFIX)] if industry.endswith(_INDUSTRY_SUFFIX) else industry


def build_classification_tags(
    tier_label: str | None, stock_id: str, industry_map: dict[str, str], concept_map: dict[str, list[str]]
) -> list[str]:
    """組出某股票要顯示的分類標籤：官方產業別（如查得到）＋市值分級（如有）＋
    人工維護的概念標籤（可能有多個，全部一併列入）。產業別排最前面，這樣同產業的
    股票即使沒有相鄰顯示，光看標籤第一個字也能一眼認出彼此是同一組。三者皆無時
    回傳空陣列，呼叫端據此決定要不要把整個 [] 省略。
    """
    tags = []
    industry = industry_map.get(stock_id)
    if industry:
        tags.append(display_industry(industry))
    if tier_label:
        tags.append(tier_label)
    tags.extend(concept_map.get(stock_id, []))
    return tags


def invert_category_table(category_table: dict) -> dict[str, list[str]]:
    """把「分類名稱 -> {members: [{stock_id, stock_name}, ...]}}」反轉成
    「股票代碼 -> 出現過的分類名稱清單」，依各分類在原始物件內的先後順序收集。
    """
    reverse: dict[str, list[str]] = {}
    for category_name, entry in category_table.items():
        for member in entry.get("members", []):
            reverse.setdefault(member["stock_id"], []).append(category_name)
    return reverse


def group_by_first_concept(items: list, stock_id_of, concept_map: dict[str, list[str]]) -> list[tuple[str | None, list]]:
    """把 items 依照每個項目對應股票的「第一個」概念分類分組（一檔股票可能同時符合多個
    概念，這裡只取分組用，不影響呈現時仍會列出全部概念）。

    回傳依序排列的 (分類名稱, 該組項目清單) 配對；組的順序＝items 中各分類第一次出現的
    順序，組內維持原始相對順序。查無概念的項目一律歸進分類名稱為 None 的那組，且該組
    固定排在最後，不管它在原始清單中第一次出現的位置在哪裡。
    """
    order: list[str | None] = []
    buckets: dict[str | None, list] = {}
    for item in items:
        concepts = concept_map.get(stock_id_of(item))
        key = concepts[0] if concepts else None
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(item)

    if None in order:
        order.remove(None)
        order.append(None)
    return [(key, buckets[key]) for key in order]


class ClassificationService:
    """維護 data/reference/industry_tags.json。只針對呼叫端指定的股票代碼查詢：本地
    已有分類的直接沿用、不重打 API；本地沒有的才查 FinMind。查詢失敗或查無資料時，
    這檔股票這次就是不會出現在回傳結果裡，不會讓其他股票的查詢或整體通知流程中斷，
    也不會在本地表留下「查無資料」的負面紀錄，下次還有機會重新查到。
    """

    def __init__(self, finmind_client: FinMindClient, storage: SnapshotRepository):
        self._finmind_client = finmind_client
        self._storage = storage

    def ensure_industry_categories(self, stock_ids: list[str]) -> dict[str, str]:
        table = self._storage.read_industry_tags()
        known = self._known_industries(table)

        result: dict[str, str] = {}
        table_changed = False
        for stock_id in stock_ids:
            if stock_id in known:
                result[stock_id] = known[stock_id]
                continue

            info = self._fetch_industry(stock_id)
            if info is None:
                continue

            self._add_member(table, info)
            result[stock_id] = info["industry_category"]
            table_changed = True

        if table_changed:
            self._storage.write_industry_tags(table)
        return result

    @staticmethod
    def _known_industries(table: dict) -> dict[str, str]:
        """官方產業別互斥，反查結果每檔股票理論上只會有一個分類，取第一筆即可。"""
        return {stock_id: categories[0] for stock_id, categories in invert_category_table(table).items()}

    def _fetch_industry(self, stock_id: str) -> dict | None:
        try:
            info = self._finmind_client.fetch_stock_industry(stock_id)
        except Exception as exc:  # noqa: BLE001 - 單一股票查詢失敗不能拖累其他股票，也不該中止通知流程
            logger.warning("產業別查詢失敗（%s）：%s", stock_id, exc)
            return None
        if info is None or not info.get("industry_category"):
            logger.info("產業別查無資料（%s），本次不附加產業標籤", stock_id)
            return None
        return info

    def _add_member(self, table: dict, info: dict) -> None:
        entry = table.setdefault(info["industry_category"], {"members": [], "updated_at": None})
        entry["members"].append({"stock_id": info["stock_id"], "stock_name": info["stock_name"]})
        entry["updated_at"] = self._now()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
