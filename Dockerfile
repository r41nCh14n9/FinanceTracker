# 本機/排程共用的執行環境映像檔。
#
# 刻意採用與 .github/workflows/daily-chip-monitor.yml 相同的 Python 版本（3.11），
# 讓「本機 Docker 測試」的行為盡量貼近正式 GitHub Actions 排程實際執行環境——
# 這支映像檔的第一個用途就是驗證本機開發環境（Python 3.14）踩到的 TPEx TLS 憑證問題
# 是否為本機特有，詳見 docs/design/architecture/SD-每日完整籌碼報告與漲跌停監控-系統設計書.md 第六章。
#
# 第二個用途是作為本機排程觸發 server 的基礎映像檔（見 docker-compose.yml 的 scheduler 服務、
# docker/entrypoint.sh、docker/crontab），在沒有 GitHub Actions（或想要獨立於它之外）的情況下，
# 於本機／自架主機上以容器常駐執行每日排程。
FROM python:3.11-slim

# 容器預設時區改為台灣時間，讓 docker/crontab 內的排程時間可以直接寫台灣本地時間，
# 不需要像 GitHub Actions workflow 那樣額外換算成 UTC（該檔案內有對應註解可互相參照）。
ENV TZ=Asia/Taipei
RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata \
    && ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime \
    && echo "$TZ" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先只複製 requirements.txt 再安裝套件，讓「改程式碼但沒改相依套件」時可以命中 Docker layer cache，
# 不需要每次都重新 pip install。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY src/ ./src/
COPY config/ ./config/
COPY tests/ ./tests/
COPY docker/ ./docker/

RUN chmod +x docker/entrypoint.sh

# data/ 刻意不 COPY 進映像檔：正式資料一律透過 docker-compose 掛載本機 ./data，
# 容器本身不內建、也不會把任何快照資料一起打包進映像檔。
# .env 同樣不 COPY 進來，一律於執行期透過 docker-compose 的 env_file／`docker run --env-file` 注入，
# 避免密鑰被 bake 進映像層（image layer 有機會被誤 push 到外部 registry）。

# 預設行為＝一次性執行主流程；scheduler 服務會在 docker-compose.yml 覆寫成 docker/entrypoint.sh。
ENTRYPOINT ["python", "main.py"]
