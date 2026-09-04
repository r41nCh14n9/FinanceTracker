#!/usr/bin/env bash
# 本機 Docker 測試腳本：在跟 GitHub Actions workflow 相同的 Python 3.11 環境下驗證外部連線
# 問題（例如本機 Python 3.14 環境踩到的 TPEx TLS 憑證錯誤是否為本機特有，見
# docs/design/architecture/SD-每日完整籌碼報告與漲跌停監控-系統設計書.md 第六章待確認事項），
# 或單純不想動到本機既有 .venv（3.14）就跑一次測試/dry-run。
#
# 用法：scripts/docker-test.sh [模式] [額外參數...]
#   scripts/docker-test.sh                              # 預設模式 env-check：印出容器內 Python/OpenSSL
#                                                          版本，並實際呼叫 TWSE／TPEx 端點驗證連線
#   scripts/docker-test.sh test -k analyzer              # 在容器內跑 pytest（額外參數原樣轉給 pytest）
#   scripts/docker-test.sh dry-run --date 2026-08-31     # 在容器內跑 main.py --dry-run（需要專案根目錄
#                                                          有 .env，額外參數原樣轉給 main.py）
#   scripts/docker-test.sh shell                         # 進容器互動式 shell，供臨時手動排查
#   scripts/docker-test.sh build                         # 只重新建置映像檔，不執行任何指令
#
# 每次執行前都會重新 build 一次映像檔（Docker layer cache 命中時很快，不會每次都重新 pip install），
# 確保跑的永遠是最新程式碼，不會發生「改了程式碼但忘記重建」而測到舊版行為的情況。
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"
cd "$project_root"

if ! command -v docker >/dev/null 2>&1; then
    echo "[docker-test.sh] 找不到 docker 指令，請先安裝 Docker Desktop（或確認已加入 PATH）" >&2
    exit 1
fi

image_tag="financetracker-test:local"

echo "[docker-test.sh] 建置映像檔 $image_tag ..."
docker build -t "$image_tag" "$project_root"

mode="${1:-env-check}"
[ $# -gt 0 ] && shift

case "$mode" in
    build)
        echo "[docker-test.sh] 映像檔已建置完成，本次不執行任何指令"
        ;;
    env-check)
        echo "[docker-test.sh] 容器內 Python／OpenSSL 版本，並實際呼叫 TWSE／TPEx 端點驗證連線..."
        docker run --rm --entrypoint python "$image_tag" -c "
import sys, ssl
print('Python:', sys.version)
print('OpenSSL:', ssl.OPENSSL_VERSION)

import requests

print()
print('--- TWSE MI_INDEX ---')
try:
    resp = requests.get(
        'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX',
        params={'date': '20260831', 'type': 'ALLBUT0999', 'response': 'json'},
        timeout=30,
    )
    print('status_code:', resp.status_code)
    print('OK，可正常連線')
except Exception as exc:
    print('連線失敗：', repr(exc))

print()
print('--- TPEx 上櫃股票每日收盤行情 ---')
try:
    resp = requests.get(
        'https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php',
        params={'l': 'zh-tw', 'd': '115/08/31', 'se': 'EW', 'o': 'json'},
        timeout=30,
    )
    print('status_code:', resp.status_code)
    d = resp.json()
    print('title:', d['tables'][0]['title'])
    print('totalCount:', d['tables'][0].get('totalCount'))
    print('OK，可正常連線（本機開發環境曾出現的 SSLCertVerificationError 未重現）')
except Exception as exc:
    print('連線失敗：', repr(exc))
    print('若此處出現 SSLCertVerificationError，代表該問題並非本機 Python 3.14 環境特有，')
    print('需列入實作階段正式處理，請回頭更新 SD 文件第六章待確認事項。')
"
        ;;
    test)
        echo "[docker-test.sh] 容器內執行 pytest..."
        docker run --rm --entrypoint python "$image_tag" -m pytest -q "$@"
        ;;
    dry-run)
        if [ ! -f ".env" ]; then
            echo "[docker-test.sh] 找不到 .env，請先依 .env.example 建立一份並填入真實密鑰" >&2
            exit 1
        fi
        echo "[docker-test.sh] 容器內執行 main.py --dry-run（真的會呼叫 FinMind／證交所 API，但不會推播 LINE）..."
        docker run --rm --env-file .env -v "$project_root/data:/app/data" "$image_tag" --dry-run "$@"
        ;;
    shell)
        echo "[docker-test.sh] 進入容器互動式 shell（exit 離開）..."
        docker run --rm -it --env-file .env -v "$project_root/data:/app/data" --entrypoint bash "$image_tag"
        ;;
    *)
        echo "[docker-test.sh] 未知模式：$mode（可用：env-check / test / dry-run / shell / build）" >&2
        exit 1
        ;;
esac
