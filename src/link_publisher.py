"""把冗長的 GitHub 報告網址縮成短網址，方便塞進 LINE 訊息。TinyURL 優先，失敗改 is.gd，
兩個免金鑰公用服務都失敗時直接回退用原始長網址本身——它是確定性字串、必定有效，
所以永遠不會有「完全沒有連結可用」的情況，呼叫端不需要另外處理「無連結」分支。
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15
_TINYURL_API = "https://tinyurl.com/api-create.php"
_ISGD_API = "https://is.gd/create.php"


class LinkPublisher:
    def shorten(self, long_url: str) -> str:
        for shorten_via in (self._via_tinyurl, self._via_isgd):
            short_url = self._try_shorten(shorten_via, long_url)
            if short_url:
                return short_url
        logger.warning("兩個縮網址服務皆失敗，改用原始網址：%s", long_url)
        return long_url

    def _try_shorten(self, shorten_via, long_url: str) -> str | None:
        try:
            return shorten_via(long_url)
        except Exception as exc:  # noqa: BLE001 - 單一服務失敗要換下一個試，不能整個拋出中斷推播
            logger.warning("縮網址服務呼叫失敗（%s）：%s", shorten_via.__name__, exc)
            return None

    @staticmethod
    def _via_tinyurl(long_url: str) -> str | None:
        resp = requests.get(_TINYURL_API, params={"url": long_url}, timeout=_REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return _as_url_or_none(resp.text)

    @staticmethod
    def _via_isgd(long_url: str) -> str | None:
        resp = requests.get(
            _ISGD_API, params={"format": "simple", "url": long_url}, timeout=_REQUEST_TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        return _as_url_or_none(resp.text)


def _as_url_or_none(response_text: str) -> str | None:
    """兩個服務成功時都是直接回傳短網址純文字，失敗時回傳一段錯誤說明文字；
    用開頭是不是 http 判斷成功與否，比嘗試解析特定錯誤格式更不容易誤判。
    """
    text = response_text.strip()
    return text if text.startswith("http") else None
