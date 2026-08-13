"""元大投信 PCF 頁面的資料來源：頁面本身是 Nuxt.js 伺服器端渲染，畫面只顯示前 5 檔，
完整的成分股清單其實已經在頁尾的 window.__NUXT__ 狀態裡，只是「展開」按鈕是純前端的
顯示筆數開關，不會另外打 API。所以這裡的做法是把整頁 HTML 存成暫存檔，交給一支
Node.js 腳本在沙箱裡把那段狀態算出來、印成 JSON，Python 這邊再讀回來解析。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import requests
import truststore

from src.issuer_pcf.base import IssuerPcfProvider

# 元大官網的憑證鏈最上層（TWCA Global Root CA）缺少 Subject Key Identifier 欄位，
# requests 預設用的 certifi 憑證清單驗證這條鏈會直接失敗；改用作業系統原生的信任庫
# （跟瀏覽器／curl 走同一套驗證邏輯）就能正常通過，所以在這裡切換掉預設驗證方式。
truststore.inject_into_ssl()

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 30
_NODE_TIMEOUT_SECONDS = 30
_USER_AGENT = "FinanceTracker-ChipMonitor/1.0"
_EXTRACT_SCRIPT_PATH = Path(__file__).parent / "scripts" / "extract_nuxt_state.js"


class YuantaPcfAdapter(IssuerPcfProvider):
    _URL_TEMPLATE = "https://www.yuantaetfs.com/tradeInfo/pcf/{etf_id}"

    def fetch_holdings(self, etf_id: str, snapshot_date: str) -> list[dict]:
        html = self._fetch_html(etf_id)
        nuxt_state = self._extract_nuxt_state(html)
        pcf_data = self._find_pcf_data(nuxt_state)

        trandate = pcf_data.get("PCF", {}).get("trandate", "")
        if trandate != snapshot_date.replace("-", ""):
            logger.warning(
                "元大 PCF 頁面交易日期（%s）與查詢日期（%s）不符，視為當日尚未更新",
                trandate, snapshot_date,
            )
            return []

        composition = pcf_data.get("InKind", {}).get("FundComposition", [])
        return [
            {
                "component_stock_id": str(row["stkcd"]),
                "component_name": row["name"],
                "holding_shares": int(row["qty"]),
            }
            for row in composition
        ]

    def _fetch_html(self, etf_id: str) -> str:
        resp = requests.get(
            self._URL_TEMPLATE.format(etf_id=etf_id),
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.text

    def _extract_nuxt_state(self, html: str) -> dict:
        # 用暫存檔而不是 NamedTemporaryFile(delete=True)：Windows 上檔案還被
        # Python 這端開著的時候，子行程開不了同一個檔案，用 mkstemp 自己控制
        # 開關時機比較保險。
        fd, tmp_path = tempfile.mkstemp(suffix=".html")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(html)

            try:
                result = subprocess.run(
                    ["node", str(_EXTRACT_SCRIPT_PATH), tmp_path],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=_NODE_TIMEOUT_SECONDS,
                    check=False,
                )
            except FileNotFoundError:
                raise RuntimeError(
                    "找不到 node 指令，Node.js 未安裝或不在 PATH，無法解析元大投信頁面"
                    "（FETCH_ISSUER_PCF_NODE_UNAVAILABLE）"
                ) from None

            if result.returncode != 0:
                raise RuntimeError(
                    "元大 PCF 頁面 Node 子行程解析失敗（FETCH_ISSUER_PCF_NUXT_EXTRACT_ERROR）："
                    f"{result.stderr.strip()}"
                )

            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "元大 PCF 頁面 Node 子行程輸出非合法 JSON"
                    f"（FETCH_ISSUER_PCF_NUXT_EXTRACT_ERROR）：{exc}"
                ) from None
        finally:
            os.unlink(tmp_path)

    @staticmethod
    def _find_pcf_data(nuxt_state: dict) -> dict:
        for entry in nuxt_state.get("fetch", []) or []:
            if isinstance(entry, dict) and "pcfData" in entry:
                return entry["pcfData"]
        raise RuntimeError(
            "元大 PCF 頁面 __NUXT__ 狀態內找不到 pcfData"
            "（FETCH_ISSUER_PCF_NUXT_EXTRACT_ERROR），網站可能已改版"
        )
