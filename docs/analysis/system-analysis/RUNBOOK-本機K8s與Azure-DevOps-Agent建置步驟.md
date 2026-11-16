# RUNBOOK：本機 K8s 與 Azure DevOps Agent 建置步驟（含低權限帳號）

| 項目 | 內容 |
| :--- | :--- |
| 對應文件 | [SA-後台管理介面與微服務化擴增-功能模組分析.md](./SA-後台管理介面與微服務化擴增-功能模組分析.md)（D-12、D-16、R-05、R-11、R-12、R-15） |
| 適用主機 | 裝置 A233／Windows 10 Home 22H2（build 19045）／Intel i5-8500／32 GB RAM |
| 狀態 | **草案，尚未在本機實測。** 指令依 Microsoft、Docker、Kubernetes 官方文件與慣例整理；每一步都有「驗證」，請逐步確認，與實際畫面不同時以官方文件為準 |
| 原則 | 日常管理員帳號（下稱 `roy`）只負責安裝與維護；**Docker Desktop、K8s、Azure DevOps agent 全部由專用標準使用者 `ftops` 執行**，不加入 Administrators |

> 為什麼要專用帳號？① Azure DevOps pipeline 會在 agent 上執行任意指令，agent 若是管理員＝任何能改 pipeline 的人都等於取得整台電腦；② Windows Home 的 Docker Desktop 與其 WSL 環境、K8s、image、資料庫儲存空間都**綁定在啟動它的那個 Windows 使用者**，固定用一個帳號才不會「換帳號登入就看不到叢集與資料」。

---

## 0. 一次性決策（先確認）

| 項目 | 建議值 | 備註 |
| :--- | :--- | :--- |
| 專用帳號 | `ftops`（標準使用者） | 見 §2 |
| 開機時間（BIOS） | **04:00**（已確認） | 與約 03:00 的關機相隔約 1 小時；與發送時間 19:30 相隔很久，緩衝充足 |
| 通知發送時間／重試 | 各通知事項自訂（預設 19:30）、每 30 分鐘重試、截止 02:00 | 後台可改 |
| 關機前處理 | 02:50 | 備份 02:30 後 |
| Windows 關機 | 03:00（你現有排程；若實際 03:00～04:00，以最早者為準） | |
| K8s Namespace | `financetracker` | |
| Agent 資源池／名稱 | `local-k8s-pool`／`A233-agent` | |

---

## 1. 前置檢查（以 `roy` 執行）

```powershell
wsl --status
wsl --version
systeminfo | findstr /i "Hyper-V 需求"     # 或工作管理員→效能→CPU 確認「虛擬化：已啟用」
```

驗證：`wsl --status` 顯示預設版本 2；虛擬化為「已啟用」。你已繞過 Microsoft Store 安裝 WSL 成功，這裡只是確認。

---

## 2. 建立低權限帳號 `ftops`

Windows 10 **Home 沒有**「本機使用者和群組」圖形介面（`lusrmgr.msc`），改用 PowerShell。以 `roy` 開啟「系統管理員身分」的 PowerShell：

```powershell
$pw = Read-Host -AsSecureString "請輸入 ftops 的密碼（請用長且唯一的密碼）"
New-LocalUser -Name "ftops" -Password $pw -FullName "FinanceTracker Ops" `
  -Description "Docker Desktop / K8s / Azure DevOps agent 專用" `
  -PasswordNeverExpires -AccountNeverExpires
Add-LocalGroupMember -Group "Users" -Member "ftops"
```

**不要**加入 `Administrators`。驗證：

```powershell
Get-LocalGroupMember -Group "Administrators"   # 不應出現 ftops
Get-LocalGroupMember -Group "Users"            # 應出現 ftops
```

`docker-users` 群組於 §3 安裝 Docker Desktop 後才存在，屆時再加入。

---

## 3. 安裝 Docker Desktop 並啟用 Kubernetes

1. 以 `roy`（管理員）安裝 Docker Desktop（WSL 2 後端）。個人使用適用其免費授權條件。
2. 安裝完成後，將 `ftops` 加入 `docker-users`：

   ```powershell
   Add-LocalGroupMember -Group "docker-users" -Member "ftops"
   ```
3. **登出 `roy`，以 `ftops` 登入**（第一次登入會建立使用者設定檔）。
4. 在 `ftops` 的家目錄建立 `C:\Users\ftops\.wslconfig`（限制資源，主機 32 GB 綽綽有餘）：

   ```ini
   [wsl2]
   memory=8GB
   processors=4
   swap=4GB
   ```
5. 啟動 Docker Desktop（以 `ftops`）→ Settings：
   - General：勾選 **Start Docker Desktop when you sign in to your computer**
   - General：取消勾選自動更新相關選項（Software updates → 關閉自動下載；手動於非交易日更新）
   - Kubernetes：**Enable Kubernetes**，Cluster type 選 **kubeadm**（單節點，image 與 Docker 共用，不需另行 push）
   - Apply & Restart（**不是**重開遠端主機，也不用手動關閉 Docker Desktop；僅啟用 Kubernetes 時，Docker Desktop 通常不會重啟引擎，只會顯示「安裝／啟動 Kubernetes」，屬正常現象），等待 K8s 圖示變綠（首次約 5～10 分鐘）。
   - 首次啟動若出現 Docker 登入畫面，按右上角 **Skip** 即可；本專案不需要 Docker 帳號。

   > 為什麼選 kubeadm 而不是 kind？
   > - kubeadm 的節點直接跑在 Docker Desktop 的 WSL 虛擬機內，**與 Docker 引擎共用映像檔**，`docker load` 後 Pod 即可使用；kind 的節點是獨立容器，每次部署還要多一步 `kind load docker-image`。
   > - PostgreSQL 的 PVC 資料隨 WSL 虛擬磁碟保留，機器每日關機重啟後仍在；kind 的資料綁在節點容器內，叢集一旦重建就消失。
   > - 重啟後由 Docker Desktop 自行恢復，不需額外的節點容器恢復腳本。
   > - 本專案為單機單節點、無高可用需求，kind 的多節點與快速重建優勢用不上。
6. 驗證（以 `ftops` 的 PowerShell）：

   ```powershell
   wsl -l -v
   docker version
   kubectl config use-context docker-desktop
   kubectl get nodes          # STATUS 應為 Ready
   ```

> 重要：之後**不要**用 `roy` 或其他帳號同時啟動 Docker Desktop，否則會出現兩套互不相通的環境（R-15）。

---

## 4. 每日自動重啟配套（回應「排程開關機與 Docker Desktop」）

**核心事實**：Windows 10 Home 的 Docker Desktop 是「使用者工作階段程式」，不是 Windows 服務。**沒有任何人登入＝Docker／K8s 不會啟動**。所以 BIOS 自動開機後，必須讓 `ftops` 自動登入。

### 4.1 自動登入（Autologon）

使用 Microsoft Sysinternals 的 **Autologon**（密碼以加密形式存於 LSA Secret，比手動改登錄檔明文密碼安全）：

1. 從 Microsoft Sysinternals 官網下載 Autologon，以 `roy` 管理員身分執行。
2. Username：`ftops`；Domain：本機電腦名稱（`A233`）；Password：§2 的密碼 → Enable。
3. 若系統設定了「僅允許 Windows Hello 登入」導致無法自動登入：設定 → 帳號 → 登入選項 → 關閉「為了提高安全性，僅允許對此裝置上的 Microsoft 帳戶使用 Windows Hello 登入」。

### 4.2 登入後自動鎖定螢幕（降低 Autologon 的實體風險）

以 `roy` 管理員 PowerShell，為 `ftops` 登入時建立工作（延遲 60 秒，讓 Docker Desktop 先起來）：

```powershell
$act  = New-ScheduledTaskAction -Execute "rundll32.exe" -Argument "user32.dll,LockWorkStation"
$trg  = New-ScheduledTaskTrigger -AtLogOn -User "A233\ftops"
$trg.Delay = "PT60S"
$prin = New-ScheduledTaskPrincipal -UserId "A233\ftops" -LogonType Interactive
Register-ScheduledTask -TaskName "FT-LockOnLogon" -Action $act -Trigger $trg -Principal $prin
```

鎖定後工作階段仍在，Docker Desktop、K8s、Pod 照常運作。

### 4.3 電源與更新

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 10
```

- 設定 → Windows Update → 進階選項 → **使用時間（Active hours）** 設為 18:00～03:00（Home 最長 18 小時），避免作業時段被強制重啟。
- BIOS：啟用 **Power On By RTC／RTC Alarm**（各廠牌名稱不同），設定每日開機時間（§0）。另開啟「AC 電源恢復後開機」以因應停電。

### 4.4 關機前處理（02:50）

**先建立 `C:\ftops\` 資料夾並鎖定權限**（以 `roy` 管理員 PowerShell 執行一次；此資料夾也用於 §6 的 `kubeconfig-deployer.yaml`）：

```powershell
New-Item -ItemType Directory C:\ftops
icacls C:\ftops /inheritance:r /grant:r "A233\ftops:(OI)(CI)F" "BUILTIN\Administrators:(OI)(CI)F" "NT AUTHORITY\SYSTEM:(OI)(CI)F"
icacls C:\ftops          # 確認只剩 ftops、Administrators、SYSTEM
```

> **為什麼放在 `C:\ftops\` 而不是 `C:\Users\ftops\`？** 路徑固定且簡短，排程工作與 §6 的憑證檔共用同一位置，也避開使用者設定檔路徑造成的混淆；放在 `C:\Users\ftops\` 同樣可行（設定檔預設就是私有），但需同步修改下方與 §6 的路徑。
>
> **為什麼要「僅 `ftops` 可讀寫」？**
> - **禁止寫入（防提權）**：`pre-shutdown.ps1` 會以 `ftops` 身分、`ExecutionPolicy Bypass` 每天自動執行。若其他帳號能修改它，就能在腳本裡塞入任意指令，藉排程以 `ftops` 的權限執行（而 `ftops` 可操作 Docker 與 K8s），等於繞過權限分隔並留下持久後門。
> - **禁止讀取（保護憑證）**：同資料夾的 `kubeconfig-deployer.yaml` 含 pipeline 的存取 token，被讀走就能冒用 deployer 身分操作 `financetracker` namespace。
> - 這是最小權限原則：只有該排程的執行者與管理員能碰這些檔案。但 Azure DevOps agent 也以 `ftops` 執行，等於 pipeline 能改這些檔案，所以「誰能編輯 pipeline」仍須嚴格限制（見 §7.4）。

建立 `C:\ftops\pre-shutdown.ps1`（以 `ftops` 或 `roy` 的 PowerShell 執行一次；單引號 here-string 會原樣保留內容，不會展開 `$` 與反引號）：

```powershell
@'
# 1. 要求 PostgreSQL 做 checkpoint，降低異常斷電風險
kubectl --context docker-desktop -n financetracker exec statefulset/postgres -- `
  psql -U postgres -c "CHECKPOINT;"
# 2. 正常關閉 Docker Desktop（Docker Desktop 4.37+ 提供此指令；若無，改用 Stop-Process 前先等待）
docker desktop stop
'@ | Set-Content -Encoding utf8 C:\ftops\pre-shutdown.ps1

Get-Content C:\ftops\pre-shutdown.ps1     # 確認內容
icacls C:\ftops\pre-shutdown.ps1          # 確認權限只有 ftops、Administrators、SYSTEM
```

> 注意：結尾的 `'@` 必須**頂格**（前面不可有空白），否則 PowerShell 會語法錯誤。`financetracker` namespace 與 `postgres` StatefulSet 要等 SD 階段部署後才存在，此腳本在部署前執行會報錯，屬預期。

以 `ftops` 註冊每日 02:50 工作（需「使用者登入時才執行」，因為 `ftops` 已自動登入）：

```powershell
$act = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\ftops\pre-shutdown.ps1"
$trg = New-ScheduledTaskTrigger -Daily -At 02:50
Register-ScheduledTask -TaskName "FT-PreShutdown" -Action $act -Trigger $trg -User "A233\ftops"
```

你現有的 03:00 Windows 關機排程維持不變。

### 4.5 重啟驗證（Phase 0 必做，連續 5 次）

> 本節分兩個階段。§4 在 §6（K8s 帳號）、§7（Azure DevOps agent）之前，做到這裡時應用程式**尚未部署**，`financetracker` namespace 與 Pod 都還不存在，此時執行 `kubectl get pods -n financetracker` 只會得到 `No resources found`（不代表失敗）。請先做 **階段 A**，連續 5 次的完整驗證留到**階段 B**。

**階段 A：尚未部署應用程式時（驗證開機 → 自動登入 → Docker Desktop → K8s 這條鏈路）**

重開機後**不要登入、不要操作**，10 分鐘後從手機或另一台電腦（以 `ftops` 的一般 PowerShell，勿以系統管理員身分執行）確認：

```powershell
kubectl get nodes        # STATUS 應為 Ready
kubectl get pods -A      # kube-system 底下的系統 Pod 應全部 Running
```

通過條件：主機重啟後自動登入 `ftops`（§4.1）、約 1 分鐘後鎖屏（§4.2）、節點 `Ready`、系統 Pod `Running`。

**階段 B：完成 §6、§7，並以 pipeline 部署 PostgreSQL 與應用程式（至少 §9 的 Hello World）之後，連續 5 次**

重開機後同樣不要登入、不要操作，10 分鐘後確認：

```powershell
kubectl get pods -n financetracker      # 全部 Running/Ready
```

並確認 PostgreSQL 內前一天的資料仍在、worker 在作業時段內有補跑。連續 5 次皆通過，才勾選 §9 的驗收項目。

---

## 5. 為什麼資料庫能「持久化保存」

- PostgreSQL 以 StatefulSet＋PVC 部署，Docker Desktop 預設儲存類別（hostpath）把資料放在其 WSL 虛擬磁碟內。**每日關機／重啟後資料保留**，Pod 會自動重建並掛回同一個 PVC。
- 以下動作**會刪除資料**，列為禁止：Docker Desktop 的 **Reset Kubernetes Cluster**、**Clean / Purge data**、解除安裝 Docker Desktop、刪除 `financetracker` namespace 的 PVC。
- 保險措施：每日 02:30 `pg_dump` 備份 CronJob → 寫到 Windows 目錄（如 `F:\backup\financetracker`），每週再複製一份到外接碟／雲端；**主金鑰 `APP_ENCRYPTION_KEY` 另存**，不與備份放一起。
- 不建議把 PG 資料目錄直接掛到 Windows 磁碟（NTFS 掛載會有權限與效能問題）。

---

## 6. 建立 K8s 最小權限帳號給 pipeline 使用

目的：Azure DevOps 部署時**不要**使用 Docker Desktop 預設的 cluster-admin 憑證，而是只能在 `financetracker` namespace 內操作、且**不能刪除 PVC** 的帳號。以 `ftops` 執行：

```powershell
kubectl create namespace financetracker
```

建立 `C:\ftops\deployer-rbac.yaml`（以 `ftops` 的一般 PowerShell 執行；資料夾與權限已於 §4.4 建立。以「無 BOM 的 UTF-8」寫入，避免 `kubectl` 讀到 BOM 出錯；結尾 `'@` 必須頂格）：

```powershell
$rbac = @'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: deployer
  namespace: financetracker
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: deployer
  namespace: financetracker
rules:
  - apiGroups: ["", "apps", "batch", "networking.k8s.io"]
    resources: ["deployments", "statefulsets", "replicasets", "services", "configmaps", "secrets",
                "cronjobs", "jobs", "pods", "pods/log", "pods/exec", "ingresses", "serviceaccounts"]
    verbs: ["get", "list", "watch", "create", "update", "patch"]
  - apiGroups: ["", "batch"]
    resources: ["pods", "jobs"]
    verbs: ["delete"]            # 僅允許刪 Pod/Job（清理與重啟），不含 deployments/statefulsets/pvc
  - apiGroups: [""]
    resources: ["persistentvolumeclaims"]
    verbs: ["get", "list", "watch", "create"]   # 刻意不給 update/delete，避免部署流程誤刪資料庫儲存空間
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: deployer
  namespace: financetracker
subjects:
  - kind: ServiceAccount
    name: deployer
    namespace: financetracker
roleRef:
  kind: Role
  name: deployer
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: v1
kind: Secret
metadata:
  name: deployer-token
  namespace: financetracker
  annotations:
    kubernetes.io/service-account.name: deployer
type: kubernetes.io/service-account-token
'@
[IO.File]::WriteAllText("C:\ftops\deployer-rbac.yaml", $rbac, (New-Object Text.UTF8Encoding $false))
```

```powershell
kubectl apply -f C:\ftops\deployer-rbac.yaml
# 驗證權限邊界
kubectl auth can-i create deployments -n financetracker --as=system:serviceaccount:financetracker:deployer   # yes
kubectl auth can-i delete persistentvolumeclaims -n financetracker --as=system:serviceaccount:financetracker:deployer  # no
kubectl auth can-i get nodes --as=system:serviceaccount:financetracker:deployer                                # no
```

產生 pipeline 專用 kubeconfig（`ftops` 執行；API Server 位址以 `kubectl config view` 為準，Docker Desktop 通常為 `https://kubernetes.docker.internal:6443`）：

```powershell
$token = kubectl -n financetracker get secret deployer-token -o jsonpath="{.data.token}"
$token = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($token))
$ca    = kubectl -n financetracker get secret deployer-token -o jsonpath="{.data['ca\.crt']}"
$server = kubectl config view --minify -o jsonpath="{.clusters[0].cluster.server}"

$kubeconfig = @"
apiVersion: v1
kind: Config
clusters:
- name: docker-desktop
  cluster: { server: $server, certificate-authority-data: $ca }
users:
- name: deployer
  user: { token: $token }
contexts:
- name: ft-deployer
  context: { cluster: docker-desktop, user: deployer, namespace: financetracker }
current-context: ft-deployer
"@
[IO.File]::WriteAllText("C:\ftops\kubeconfig-deployer.yaml", $kubeconfig, (New-Object Text.UTF8Encoding $false))
icacls C:\ftops\kubeconfig-deployer.yaml /inheritance:r /grant:r "A233\ftops:(R)"
```

此檔案等同密鑰：**不要 commit**；建議上傳為 Azure DevOps「Secure file」或僅保留在 agent 主機並於 pipeline 以 `KUBECONFIG` 指向。

> 注意：能執行 `docker load` 的人實際上可透過 Docker 引擎影響 WSL VM（雖不等於 Windows 管理員）。因此「誰能編輯 pipeline」仍須嚴格限制（§7.4）。

---

## 7. 安裝 Azure DevOps Self-hosted Agent（以 `ftops` 身分執行成服務）

### 7.1 Azure DevOps 端

1. Organization settings → Pipelines → **Agent pools** → Add pool → 類型 Self-hosted → 名稱 `local-k8s-pool`；**不要**勾選「Grant access permission to all pipelines」。
2. 建立 PAT：User settings → Personal access tokens → New → Scope 選 **Agent Pools (Read & manage)**，有效期 30 天；**僅用於註冊 agent**，註冊完成後可撤銷。
3. Pipelines → Environments → New → `local-k8s` → Approvals and checks → Approvals，指定你自己為核准者。

### 7.2 本機端

以 `roy`（管理員）開啟 PowerShell（註冊為 Windows 服務需要管理員權限）：

```powershell
mkdir C:\azagent
# 從 Azure DevOps Agent pools → New agent → Windows 下載 vsts-agent-win-x64-*.zip 後解壓縮到 C:\azagent
cd C:\azagent
.\config.cmd --unattended `
  --url https://dev.azure.com/<你的組織名稱> `
  --auth pat --token <PAT> `
  --pool local-k8s-pool --agent A233-agent `
  --work C:\azagent\_work `
  --runAsService `
  --windowsLogonAccount ".\ftops" --windowsLogonPassword "<ftops 密碼>"
```

目錄權限只留給 `ftops` 與系統：

```powershell
icacls C:\azagent /inheritance:r /grant:r "A233\ftops:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "BUILTIN\Administrators:(OI)(CI)F"
```

設為「自動（延遲啟動）」，避免開機初期網路／Docker 尚未就緒：

```powershell
Get-Service "vstsagent*"                     # 取得服務名稱
sc.exe config "<上一行的服務名稱>" start= delayed-auto
```

### 7.3 驗證

本節是驗證「agent 是否裝好」，**不是開始正式開發**；但要跑任何 pipeline 都必須先有 Azure DevOps **專案**與放 YAML 的儲存庫，所以在這裡建立一個最小的專案與測試 pipeline。

**前置**：§7.1 的 Agent pool 已建立；§7.2 已完成，且 agent 在 Agent pools → `local-k8s-pool` → Agents 顯示 **Online**（`A233-agent`）。

**步驟**

1. **建立專案**：`https://dev.azure.com/<你的組織名稱>` → New project → 名稱例如 `FinanceTracker`，可見性選 **Private**。
2. **準備儲存庫**（二擇一）：使用專案內建的 Azure Repos（Repos 初始化一個儲存庫），或連到既有的 GitHub `FinanceTracker`（建立 pipeline 時選 GitHub 並授權）。
3. **加入測試用 YAML**（建議放在**獨立分支**，驗證完即可刪除，避免干擾日後正式 pipeline 結構），例如 `azure-pipelines-agent-test.yml`：

   ```yaml
   trigger: none            # 不自動觸發，只手動執行

   pool:
     name: local-k8s-pool

   steps:
     - script: docker version
       displayName: Docker 可用
     - script: kubectl --kubeconfig C:\ftops\kubeconfig-deployer.yaml get pods -n financetracker
       displayName: 以 deployer 身分查詢 Pod
   ```

4. **建立 pipeline**：Pipelines → New pipeline → 選儲存庫 → Existing Azure Pipelines YAML file → 選上述檔案 → Save。
5. **授權 pool**：第一次執行時會要求 **Permit** 使用 `local-k8s-pool`，按核准。這是 §7.1 刻意不勾選「Grant access permission to all pipelines」的預期結果。
6. **Run**，檢視兩個步驟。

**通過條件**

- Agent 為 **Online**。
- `docker version` 同時顯示 Client 與 Server 資訊（代表 `ftops` 的 Docker Desktop 引擎有回應）。
- `get pods` 回傳 `No resources found in financetracker namespace.`（尚未部署應用程式，屬正常；代表 deployer kubeconfig 可用）。
- 重開機後 agent 自動回到 Online。

**註冊 agent 的注意事項**

- 建議以互動式 `.\config.cmd` 註冊（不加 `--unattended`、`--token`、`--windowsLogonPassword`），讓 PAT 與密碼以隱藏方式輸入，避免寫進 PowerShell 指令歷史；註冊完成後撤銷 PAT。
- 若 `C:\azagent` 內找不到 `config.cmd`，代表尚未下載並解壓縮 agent 安裝檔（§7.2 第二行註解）。
- 若機器上已有舊 agent，須先在舊資料夾以 `.\config.cmd remove` 正式移除並取消註冊，再刪資料夾；不要直接改名或刪除資料夾。

> 若服務帳號 `ftops` 在 Docker Desktop 尚未啟動前就接到工作，`docker` 指令會失敗。pipeline 的 CD 階段第一步請加入「等待 Docker／K8s 就緒」的重試迴圈（最多 10 分鐘），再進行 `docker load`。

### 7.4 安全守則（對應 R-05）

- 只有你能編輯 pipeline YAML 與核准 Environment；main 分支啟用分支保護。
- Pipeline 設定「不對 fork／外部 PR 執行 CD」，`local-k8s-pool` 只授權給本專案的 pipeline。
- 不把 `FINMIND_TOKEN` 等密鑰印到 log；Library 變數設為 Secret。
- PAT 註冊後立即撤銷；每季檢視 Agent 版本與權限。

---

## 8. 對外存取（LINE 連結）

| 情境 | 作法 |
| :--- | :--- |
| 只有你本人用手機看報告 | 在 `ftops` 主機與手機安裝 **Tailscale**（私有網路、不對公網開放，最安全）；連結使用 `https://<主機名>.<tailnet>.ts.net` 這類 Tailscale 網址 |
| 需要讓群組其他成員打開 | 使用 Tailscale Funnel 或 Cloudflare Tunnel 對外，並維持「全站需登入」「登入失敗鎖定」；成員需有後台帳號（SA 6-Q15） |

K8s 內 `web` 以 Docker Desktop 的 LoadBalancer／NodePort 綁在本機 port（例如 8080），Tunnel 轉發到 `localhost:8080`。細節於 SD 階段定案。

---

## 9. 完成檢查清單（Phase 0 驗收）

- [ ] `ftops` 非管理員，且 `docker-users` 成員
- [ ] `kubectl get nodes`（`ftops`）為 Ready
- [ ] Docker Desktop 設定為隨登入啟動、自動更新關閉
- [ ] Autologon 與登入後鎖屏運作；重開機 10 分鐘內 Pod 全部 Ready（連續 5 次）
- [ ] `deployer` 權限驗證：可建立 deployment、**不可刪 PVC**、不可讀 node
- [ ] Agent Online，重開機後自動恢復
- [ ] 測試 pipeline（含核准關卡）可成功部署 Hello World 到 `financetracker`
- [ ] 預關機工作 02:50 能成功執行（手動執行一次驗證）
- [ ] 備份目錄可寫入；備份與主金鑰分開保管
- [ ] BIOS 排程開機時間已確認，並登錄於後台系統設定（UC-15）
