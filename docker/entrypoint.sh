#!/usr/bin/env bash
# scheduler 容器的進入點：把 docker-compose 透過 env_file 注入的密鑰環境變數轉寫進
# /etc/environment（cron 執行 job 的 shell 不會繼承本 script 當下的環境變數，需要這一步
# 才能讓 docker/crontab 內的 `. /etc/environment` 讀得到 FINMIND_TOKEN 等密鑰），
# 載入排程表後常駐執行 cron，並把主流程的輸出即時導到容器自身的 stdout，
# 讓 `docker compose logs -f scheduler` 能看到每次排程執行的結果。
set -euo pipefail

mkdir -p /app/data
touch /app/data/scheduler.log

# 只挑選本流程需要的三個密鑰變數寫入 /etc/environment，避免把容器內其他不相關的環境變數
# （如 PATH、HOSTNAME 等）也一併寫入造成混亂或覆蓋容器原本行為。
printenv | grep -E '^(FINMIND_TOKEN|LINE_CHANNEL_ACCESS_TOKEN|LINE_CHANNEL_SECRET)=' >> /etc/environment || true

crontab /app/docker/crontab

echo "[entrypoint] 排程已載入（週一至週五 台灣時間 19:30 執行主流程，19:31 執行保留清除），容器持續執行中..."
echo "[entrypoint] 目前排程內容："
crontab -l

# cron 前景常駐執行；scheduler.log 用 tail -F 持續輸出到容器 stdout，
# 兩者都是長駐前景程序，最後用 wait 讓其中任一個結束時容器才跟著結束（避免容器裝死也偵測不到）。
cron
tail -F /app/data/scheduler.log &
TAIL_PID=$!
wait "$TAIL_PID"
