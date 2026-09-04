#!/usr/bin/env bash
# 本機執行腳本：建立/啟用虛擬環境、安裝套件，再依模式跑測試或真的執行主流程。
# 用法：scripts/run.sh [模式] [main.py 參數 / pytest 參數...]
#   scripts/run.sh                                  # 預設模式 test：跑 pytest 單元測試
#   scripts/run.sh test -k analyzer                 # 只跑符合關鍵字的測試
#   scripts/run.sh 0 --date 2026-07-28               # 完整流程：真的抓外部資料 + 真的推播 LINE（單次跑完，不分兩階段）
#   scripts/run.sh full --date 2026-07-28            # 同上（0 的別名）
#   scripts/run.sh 1 --date 2026-07-28               # 真的抓外部資料，但不推播 LINE
#   scripts/run.sh fetch-only --date 2026-07-28      # 同上（1 的別名）
#   scripts/run.sh 2                                 # 準備並檢查 .env，缺什麼變數就報錯退出
#   scripts/run.sh check                             # 同上（2 的別名）
#   scripts/run.sh skip-notify --date 2026-07-28     # 兩階段流程的第一段：抓取/分析/產出完整版報告，不推播
#   scripts/run.sh notify-only --date 2026-07-28     # 兩階段流程的第二段：讀回既有分析結果格式化並推播；
#                                                       可加 --report-url 測試短網址附加效果
#   scripts/run.sh purge                             # 只清除超過保留天數的舊快照/報告目錄，不跑抓取/分析/推播
#   scripts/run.sh purge --dry-run                   # 同上，但只印出會清除哪些目錄，不實際刪除
#
# 模式 0 / 1 / skip-notify / notify-only 都會真的呼叫 FinMind / 證交所 / TWSE / TPEx / 短網址服務等
# 外部 API，執行前會先做模式 2 的檢查。
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"
cd "$project_root"

venv_dir=".venv"
if [ ! -d "$venv_dir" ]; then
    echo "[run.sh] 找不到虛擬環境，建立 $venv_dir ..."
    python -m venv "$venv_dir"
fi

if [ -f "$venv_dir/Scripts/activate" ]; then
    source "$venv_dir/Scripts/activate"   # Windows（含 Git Bash）
elif [ -f "$venv_dir/bin/activate" ]; then
    source "$venv_dir/bin/activate"       # macOS / Linux
else
    echo "[run.sh] 虛擬環境已建立但找不到 activate 腳本，中止" >&2
    exit 1
fi

pip install -q -r requirements.txt

required_env_vars=(FINMIND_TOKEN LINE_CHANNEL_ACCESS_TOKEN LINE_CHANNEL_SECRET)

prepare_env_file() {
    if [ -f ".env" ]; then
        return
    fi
    if [ ! -f ".env.example" ]; then
        echo "[run.sh] 找不到 .env，也找不到 .env.example 可供複製，中止" >&2
        exit 1
    fi
    echo "[run.sh] 找不到 .env，依 .env.example 建立一份..."
    cp ".env.example" ".env"
}

check_env_vars() {
    prepare_env_file

    set -a
    source ".env"
    set +a

    local missing=()
    for key in "${required_env_vars[@]}"; do
        if [ -z "${!key:-}" ]; then
            missing+=("$key")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        echo "[run.sh] .env 缺少以下必要環境變數的值：${missing[*]}" >&2
        echo "[run.sh] 請編輯 .env 補齊後再執行" >&2
        exit 1
    fi
    echo "[run.sh] 環境變數檢查通過：${required_env_vars[*]} 皆已設定"
}

mode="${1:-test}"
[ $# -gt 0 ] && shift

case "$mode" in
    test)
        echo "[run.sh] 執行 pytest..."
        python -m pytest -q "$@"
        ;;
    2|check)
        check_env_vars
        ;;
    0|full)
        check_env_vars
        echo "[run.sh] 完整流程：真的抓外部資料 + 真的推播 LINE..."
        python main.py "$@"
        ;;
    1|fetch-only)
        check_env_vars
        echo "[run.sh] 真的抓外部資料，但不推播 LINE（main.py --dry-run）..."
        python main.py --dry-run "$@"
        ;;
    purge)
        check_env_vars
        echo "[run.sh] 清除超過保留天數的舊快照/報告目錄..."
        python main.py --purge "$@"
        ;;
    skip-notify)
        check_env_vars
        echo "[run.sh] 兩階段流程第一段：抓取/分析/產出完整版報告，不推播（main.py --skip-notify）..."
        python main.py --skip-notify "$@"
        ;;
    notify-only)
        check_env_vars
        echo "[run.sh] 兩階段流程第二段：讀回既有分析結果格式化並推播（main.py --notify-only）..."
        python main.py --notify-only "$@"
        ;;
    *)
        echo "[run.sh] 未知模式：$mode（可用：test / 0|full / 1|fetch-only / 2|check / skip-notify / notify-only / purge）" >&2
        exit 1
        ;;
esac
