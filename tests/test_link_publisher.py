from unittest.mock import MagicMock, patch

from src.link_publisher import LinkPublisher

_LONG_URL = "https://github.com/r41nCh14n9/FinanceTracker/blob/main/data/reports/2026-09-01/daily_report.md"


def _fake_response(text: str, status_ok: bool = True):
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status.return_value = None if status_ok else None
    if not status_ok:
        resp.raise_for_status.side_effect = Exception("boom")
    return resp


def test_shorten_returns_tinyurl_result_when_successful():
    with patch("src.link_publisher.requests.get", return_value=_fake_response("https://tinyurl.com/abc123")):
        result = LinkPublisher().shorten(_LONG_URL)

    assert result == "https://tinyurl.com/abc123"


def test_shorten_falls_back_to_isgd_when_tinyurl_fails():
    def side_effect(url, **kwargs):
        if url == "https://tinyurl.com/api-create.php":
            raise Exception("timeout")
        return _fake_response("https://is.gd/xyz789")

    with patch("src.link_publisher.requests.get", side_effect=side_effect):
        result = LinkPublisher().shorten(_LONG_URL)

    assert result == "https://is.gd/xyz789"


def test_shorten_falls_back_to_original_url_when_both_services_fail():
    with patch("src.link_publisher.requests.get", side_effect=Exception("boom")):
        result = LinkPublisher().shorten(_LONG_URL)

    assert result == _LONG_URL


def test_shorten_treats_non_url_response_as_failure():
    """服務回應成功（HTTP 200）但內容不是網址（例如額度用盡的錯誤訊息），
    也要當成失敗換下一個服務，不能把錯誤訊息文字誤當成短網址回傳。
    """
    def side_effect(url, **kwargs):
        if url == "https://tinyurl.com/api-create.php":
            return _fake_response("Error: rate limit exceeded")
        return _fake_response("https://is.gd/xyz789")

    with patch("src.link_publisher.requests.get", side_effect=side_effect):
        result = LinkPublisher().shorten(_LONG_URL)

    assert result == "https://is.gd/xyz789"


def test_shorten_calls_isgd_with_simple_format():
    def side_effect(url, **kwargs):
        if url == "https://tinyurl.com/api-create.php":
            raise Exception("timeout")
        assert kwargs["params"] == {"format": "simple", "url": _LONG_URL}
        return _fake_response("https://is.gd/xyz789")

    with patch("src.link_publisher.requests.get", side_effect=side_effect):
        LinkPublisher().shorten(_LONG_URL)
