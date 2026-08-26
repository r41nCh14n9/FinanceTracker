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
from src.storage import SnapshotRepository

logger = logging.getLogger(__name__)


def invert_category_table(category_table: dict) -> dict[str, list[str]]:
    """把「分類名稱 -> {members: [{stock_id, stock_name}, ...]}}」反轉成
    「股票代碼 -> 出現過的分類名稱清單」，依各分類在原始物件內的先後順序收集。
    """
    reverse: dict[str, list[str]] = {}
    for category_name, entry in category_table.items():
        for member in entry.get("members", []):
            reverse.setdefault(member["stock_id"], []).append(category_name)
    return reverse


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
