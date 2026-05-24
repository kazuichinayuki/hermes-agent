# Codex /goal × Decohere 數據可管理化方案

**日期:** 2026-05-09
**狀態:** 計劃階段（plan mode，不執行）

---

## 目標

1. 研究 Codex CLI `/goal` 功能，提取可操作的設計模式
2. 設計 Decohere 數據存儲的查詢/修改/刪除方案，使其從黑箱變為可管理資產
3. 產出三份詳盡的 `/goal` 提示詞，以 Codex 自主循環實現上述方案

---

## 一、Codex /goal 功能摘要

### 核心機制

| 維度 | 詳情 |
|------|------|
| 版本 | Codex CLI ≥ v0.128.0（2026-05-02） |
| 啟用 | `~/.codex/config.toml` 加 `[features] goals = true` |
| 生命週期 | `active → paused → budget_limited → done` |
| 持久性 | 目標存於 database，跨終端重啟、跨 model 切換、跨 token budget 耗盡 |
| 驗證循環 | `plan → act → test → review`，直到可測量的停止條件滿足 |
| 命令 | `/goal <objective>` `/goal` `/goal pause` `/goal resume` `/goal clear` |

### 成功條件（決定性因素）

> **"Codex can check its own progress against evidence. If you can't write that line, you don't have a /goal. You have a prompt."**

- 測試通過、覆蓋率上升、lighthouse 分數改善、截圖匹配、檔案輸出驗證
- 適合：遷移、測試擴展、TDD、部署重試、重構+驗證、性能循環
- 不適合：探索性設計、架構決策、需要品味的任務、模糊的停止條件

### 提示詞結構模式

成功 `/goal` 提示詞的模板來自實戰案例：

```
/goal <一句話目標>
  
<可測量的停止條件> — 必須是可被代碼驗證的命題

Required workflow loop:
1. 步驟一（可觀測輸出）
2. 步驟二（可觀測輸出）
...

Hard constraints: <邊界約束>

Stop signal: <精確的停止條件表達式>

Hard cap: <時間或 token 上限>
```

---

## 二、Decohere 當前狀態

```
數據存儲：SQLite @ <hermes_home>/sessions/<session_id>/decohere.db
├── ledger_entries   (turn_n, entry_json, posted_at, validated)
├── concepts_fts      (FTS5 全文索引)
├── raw_messages      (store_id, role, content, tool_name, tool_call_id, timestamp)
└── metadata          (key, value)

當前可操作性：零。只能通過 sqlite3 命令行手動查詢。
```

### 需要暴露的操作

| 操作 | 含義 | 示例 |
|------|------|------|
| **list** | 列出所有 session 或某 session 的所有 entries | `hermes decohere list --session <id>` |
| **show** | 顯示某個 entry 的完整 JSON | `hermes decohere show --turn 5` |
| **search** | 全文搜索 concepts 或 narrative | `hermes decohere search "context window"` |
| **edit** | 修改某 entry 的欄位 | `hermes decohere edit --turn 5 --field user_intent --value "..."` |
| **delete** | 刪除某個 entry 或整個 session | `hermes decohere delete --turn 5` |
| **export** | 匯出為 JSON/Markdown | `hermes decohere export --format md` |
| **stats** | 統計摘要：總 turns、平均 tokens、最頻繁概念 | `hermes decohere stats` |

---

## 三、技術方案設計

### CLI 命令架構

```
hermes decohere
├── list    [--session <id>] [--limit N]
├── show    --turn N [--session <id>] [--layer l1|l2|full]
├── search  <query> [--session <id>] [--field concepts|narrative|all]
├── edit    --turn N [--session <id>] --field <name> --value <json>
├── delete  --turn N [--session <id>] [--confirm]
├── export  [--session <id>] [--format json|md|yaml] [--output <path>]
├── stats   [--session <id>]
└── sessions  (列出所有有 decohere.db 的 session)
```

### 實作層

所有命令操作同一個 SQLite DB，唯讀操作不加鎖，寫操作（edit/delete）使用 WAL 模式的事務。

- **list/show/search/export/stats/sessions** → 只讀，直接 SQL
- **edit** → `UPDATE ledger_entries SET entry_json = ? WHERE turn_n = ?`，同步更新 FTS5
- **delete** → `DELETE FROM ledger_entries WHERE turn_n = ?`，同步清理 FTS5 和 raw_messages

### 安全約束

- edit/delete 必須 `--confirm` 或有交互式確認
- 不可逆操作記錄到 `.decohere_audit.log`
- 修改後的 entry 標記 `validated = 0` 以便後續審查

---

## 四、三份 /goal 提示詞

## 四、方案 A 詳細規格：CLI 工具鏈

### 4.0 多 Profile 設計（關鍵）

Decohere 的 sessions 存儲在 `HERMES_HOME/sessions/<id>/decohere.db`。默認 profile 的 HERMES_HOME 是 `~/.hermes`，speak-off-the-cuff 是 `~/.hermes/profiles/speak-off-the-cuff`。**數據天生按 profile 隔離，但 CLI 必須顯式尊重這個邊界。**

#### Profile 解析優先級

```
1. --profile <name>   → hermes profile show <name> 獲取 home 路徑
2. --home <path>      → 直接使用指定路徑
3. 環境變量 HERMES_HOME → 若存在則使用
4. 默認                → ~/.hermes（全局 profile）
```

實現：調用 `hermes profile show <name> --json` 獲取 `{"home": "/Users/.../profiles/<name>"}`，再從 `{home}/sessions/` 讀取 DB。

#### 每個命令的 profile 感知

所有八個子命令新增 `--profile <name>` 選項，與 `--home` 互斥。

```bash
# 指定 profile
hermes decohere sessions --profile speak-off-the-cuff
hermes decohere list --profile speak-off-the-cuff --limit 10
hermes decohere show --profile speak-off-the-cuff --turn 3

# 或直接指定路徑
hermes decohere sessions --home ~/.hermes/profiles/speak-off-the-cuff

# 預設：當前活躍 profile（hermes profile use 設定的）
hermes decohere sessions
```

#### `sessions` 命令的 profile 視角

```
$ hermes decohere sessions
Active profile: speak-off-the-cuff (~/.hermes/profiles/speak-off-the-cuff)
  SESSION_ID                    TURNS  RAW_MSGS  LAST_UPDATED
  20260510_175019_e0218857      12     234       2026-05-10 17:50:19
  ...

$ hermes decohere sessions --all-profiles
  PROFILE          SESSION_ID                    TURNS
  speak-off-the-cuff  20260510_175019_e0218857      12
  speak-off-the-cuff  20260509_192623_aeee5e        5
  default             20260510_175529_85313e        8
  default             20260509_180607_4b912032      5
```

`--all-profiles` 選項掃描所有已知 profile（通過 `hermes profile list --json`）的 sessions 目錄。

#### `_shared.py` 的 resolve_session 更新

```python
def resolve_hermes_home(profile: str | None = None, home: str | None = None) -> Path:
    """解析 hermes_home 路徑，按優先級：profile > home > env > default。
    
    當指定 profile 時，調用 `hermes profile show <name> --json` 獲取路徑。
    """
```

#### `hermes decohere sessions`

```
用法: hermes decohere sessions [--profile <name>] [--home <path>] [--all-profiles] [--json]

列出 sessions 目錄下所有包含 decohere.db 的 session。

選項:
  --profile NAME  使用指定 profile 的 sessions（優先於 --home）
  --home PATH     直接指定 hermes_home 路徑（預設：活躍 profile）
  --all-profiles  掃描所有已知 profile 的 sessions
  --json          輸出 JSON 而非表格

輸出（表格模式，單 profile）:
  Active profile: speak-off-the-cuff (~/.hermes/profiles/speak-off-the-cuff)
  SESSION_ID                    TURNS  RAW_MSGS  LAST_UPDATED
  20260509_192623_aeee5e        12     234       2026-05-09 20:15:03

輸出（表格模式，--all-profiles）:
  PROFILE              SESSION_ID                    TURNS  RAW_MSGS
  speak-off-the-cuff   20260510_175019_e0218857      12     234
  speak-off-the-cuff   20260509_192623_aeee5e        5      98
  default              20260510_175529_85313e        8      156

特殊情況:
  - 無任何 session → "(no sessions found)"，exit 0
  - profile 不存在 → "Profile '<name>' not found. Use 'hermes profile list' to see available profiles."，exit 1
  - hermes_home 不存在 → 錯誤訊息，exit 1
```

#### `hermes decohere list`

```
用法: hermes decohere list [--profile <name>] [--home <path>] [--session <id>] [--limit N] [--offset N] [--layer l1|l2|full] [--json]

列出 ledger entries 摘要。

選項:
  --profile NAME  使用指定 profile（優先於 --home）
  --home PATH     直接指定 hermes_home 路徑（預設：活躍 profile）
  --session ID    指定 session（預設：最近修改的 session）
  --limit N       最多返回 N 條（預設 20）
  --offset N      從第 N 條開始（預設 0）
  --layer l1|l2|full  控制顯示層級（預設 full）
  --json          輸出 JSON

輸出（表格模式，layer=full）:
  TURN  TASK              TOOLS              FILES     POSTED
  1     vision debug       vision_analyze     none      19:26:30
  2     config fix         patch, terminal    config..  19:28:15

輸出（JSON 模式）:
  [{"turn_n": 1, "task": "...", "tools": [...], "files": [...], "posted_at": ...}, ...]

特殊情況:
  - session 無 entries → "Session <id>: 0 ledger entries"，exit 0
  - session 不存在 → 錯誤，exit 1
```

#### `hermes decohere show`

```
用法: hermes decohere show --turn N [--session <id>] [--layer l1|l2|full] [--json]

顯示單個 entry 的完整內容。

選項:
  --turn N        必需，turn 編號（1-indexed）
  --session ID    預設：最近修改的 session
  --layer l1|l2|full  l1=僅 reference_documentation + relevant_metadata
                      l2=僅 concepts/narrative/decisions/procedures/insights/critical_reflection
                      full=全部（預設）
  --json          輸出 JSON（預設為格式化的 YAML 風格輸出）

輸出（layer=full，非 JSON 模式）:
  ── Turn 5 ─────────────────────────────────
  message_range: [89, 104]
  tools: read_file, patch, terminal
  files: ~/.hermes/config.yaml

  task: compression threshold fix
  ref_class: config

  ── Concepts ──
  • context window: The maximum token capacity of an LLM's input
  • threshold_percent: Fraction of context window at which compression triggers

  ── Narrative ──
  Fixed the compression threshold mismatch by reading compression.threshold
  from config.yaml in on_session_start()...

  ── Decisions ──
  • Set threshold_percent from config instead of hardcoding 1.0
    → compression.threshold was being ignored by decohere

  ── Procedures ──
  • Modified __init__.py on_session_start to read compression block

  ── Insights ──
  • Decohere's threshold_percent default of 1.0 bypasses config

  ── User Intent ──
  Fix the persistent compression warning by wiring config threshold into decohere

  ── Critical Reflection ──
  ↳ improvements:
  • Should also validate threshold range (0.0-1.0)

特殊情況:
  - turn 不存在 → "Turn N not found in session <id>"，exit 1
```

#### `hermes decohere search`

```
用法: hermes decohere search <query> [--session <id>] [--field concepts|narrative|all] [--limit N] [--json]

全文搜索 concepts_and_definitions 或 narrative。

選項:
  query           必需，FTS5 查詢字串（支援布林運算）
  --session ID    預設：最近修改的 session；可多次指定
  --field FIELD   搜索字段（預設 all）
                  concepts = 僅 FTS5 concepts_fts
                  narrative = 僅 SQL LIKE on entry_json
                  all = 兩者
  --limit N       最多返回 N 條（預設 10）
  --json          輸出 JSON

FTS5 查詢語法:
  context window          → 包含 "context" 或 "window" 的條目
  "context window"        → 精確匹配短語
  compression NOT config  → 布林排除

輸出（表格模式）:
  TURN  MATCH_TYPE  MATCH
  3     concept     context window: The maximum token capacity...
  7     concept     compression threshold: Token budget at which...
  7     narrative   "...compression model context is 400,000 tokens..."

特殊情況:
  - 無匹配 → "No results for '<query>'"，exit 0
  - FTS5 查詢語法錯誤 → 顯示 sqlite3 錯誤訊息，exit 1
```

#### `hermes decohere edit`

```
用法: hermes decohere edit --turn N [--session <id>] --field <name> --value <json> [--confirm]

修改某個 entry 的欄位。

選項:
  --turn N        必需
  --session ID    預設：最近修改的 session
  --field NAME    必需，支援巢狀路徑（如 narrative.summary, concepts_and_definitions[0].term）
  --value JSON    必需，新值（JSON 格式）
  --confirm       跳過交互式確認（用於腳本/Codex）

支援的欄位路徑:
  user_intent                                    → "新的意圖文字"
  narrative.summary                              → "新的摘要"
  concepts_and_definitions[0].term               → "修正的術語"
  concepts_and_definitions[0].definition         → "修正的定義"
  decisions_and_rationale[0].decision            → "修正的決策"
  relevant_metadata.task                         → "新的任務描述"
  critical_reflection.improvement_directions     → ["改進1", "改進2"]

交互式確認流程:
  ⚠ About to edit Turn 5, field 'narrative.summary'
  Old value: "Fixed the compression threshold mismatch..."
  New value: "修正後的摘要文字..."
  Proceed? [y/N]

審計日誌:
  每次 edit 在 <hermes_home>/decohere_audit.log 寫入一行 JSON:
  {"ts": "2026-05-09T22:15:00", "session": "...", "turn": 5,
   "field": "narrative.summary", "old": "...", "new": "..."}

事務保證:
  - BEGIN → UPDATE entry_json → 重建 concepts_fts → 寫審計日誌 → COMMIT
  - 任何步驟失敗 → ROLLBACK
  - 修改後 validated 設為 0

特殊情況:
  - field 不存在 → "Field '<name>' not found in Turn N"，exit 1
  - value JSON 解析失敗 → "Invalid JSON: ..."，exit 1
  - entry 不存在 → "Turn N not found"，exit 1
```

#### `hermes decohere delete`

```
用法: hermes decohere delete --turn N [--session <id>] [--confirm]
       hermes decohere delete --session <id> --all [--confirm]

刪除單個 entry 或整個 session 的數據。

單個刪除:
  --turn N        必需
  --session ID    預設：最近修改的 session
  --confirm       跳過交互式確認

批量刪除:
  --session ID    必需
  --all           刪除該 session 的所有 entries + raw_messages

交互式確認（單個）:
  ⚠ About to DELETE Turn 5 from session <id>.
  This action is irreversible.
  Proceed? [y/N]

交互式確認（批量）:
  ⚠ About to DELETE ALL 12 ledger entries and 234 raw messages
  from session <id>. This action is irreversible.
  Type the session ID to confirm: ____

事務保證:
  - 單個：DELETE ledger_entries → DELETE concepts_fts → 寫審計日誌 → COMMIT
  - 批量：DELETE ALL ledger_entries → DELETE ALL concepts_fts → DELETE ALL raw_messages → COMMIT

審計日誌:
  {"ts": "...", "session": "...", "turn": 5, "action": "delete"}
  {"ts": "...", "session": "...", "action": "delete_all", "entry_count": 12, "raw_count": 234}

特殊情況:
  - turn 不存在 → "Turn N not found"，exit 1
  - session 無 entries → "Session <id> has no entries to delete"，exit 0
```

#### `hermes decohere export`

```
用法: hermes decohere export [--session <id>] [--format json|md|yaml] [--output <path>] [--layer l1|l2|full]

匯出 ledger entries 到檔案。

選項:
  --session ID    預設：最近修改的 session
  --format FORMAT  json | md | yaml（預設 md）
  --output PATH   輸出檔案路徑（預設 stdout）
  --layer l1|l2|full  匯出的層級（預設 full）

Markdown 輸出範例:
  # Decohere Ledger Export — session 20260509_192623_aeee5e
  Exported: 2026-05-09 22:30:00 | 12 turns

  ## Turn 1 — vision debug
  **Task:** vision debug | **Tools:** vision_analyze | **Files:** none
  ...

JSON 輸出範例:
  {"session_id": "...", "exported_at": "...", "turn_count": 12,
   "turns": [{"turn_n": 1, ...}, ...]}

特殊情況:
  - output 目錄不存在 → 自動建立
  - 寫入權限不足 → 錯誤，exit 1
```

#### `hermes decohere stats`

```
用法: hermes decohere stats [--session <id>] [--json]

顯示 session 的統計摘要。

選項:
  --session ID    預設：最近修改的 session
  --json          輸出 JSON

輸出（非 JSON 模式）:
  Session: 20260509_192623_aeee5e
  ─────────────────────────────────
  Total turns:           12
  Total raw messages:    234
  Avg messages/turn:     19.5
  Pending entries:       2        (critical_reflection = null)
  Validated entries:     10
  Top tools:             terminal(7), patch(4), read_file(3), vision_analyze(1)
  Top concepts:          context window(2), compression threshold(2), ledger(1)
  Storage:
    ledger_entries:      48.3 KB
    raw_messages:        156.2 KB
    concepts_fts:        4.1 KB
    Total DB:            208.6 KB
  First turn:            2026-05-09 19:26:30
  Last turn:             2026-05-09 20:28:15

特殊情況:
  - session 無 entries → "Session <id>: no ledger entries"，stats 全為 0
```

---

### 4.2 內部架構

#### 模組結構

```
plugins/context_engine/decohere/cli/
├── __init__.py          # Click 命令組定義 + 註冊入口
├── _shared.py            # 共用工具：resolve_session(), open_db(), format_timestamp()
├── sessions_cmd.py       # hermes decohere sessions
├── list_cmd.py           # hermes decohere list
├── show_cmd.py           # hermes decohere show
├── search_cmd.py         # hermes decohere search
├── edit_cmd.py           # hermes decohere edit
├── delete_cmd.py         # hermes decohere delete
├── export_cmd.py         # hermes decohere export
└── stats_cmd.py          # hermes decohere stats
```

#### `_shared.py` 共用工具

```python
# 會在所有命令中共用的函數

def resolve_hermes_home(profile: str | None = None, home: str | None = None) -> Path:
    """解析 hermes_home 路徑。
    
    優先級：profile > home > HERMES_HOME env > ~/.hermes
    
    當指定 profile 時，調用 `hermes profile show <name> --json`
    解析輸出的 {"home": "..."} 獲取路徑。
    若 profile 不存在，拋出 ProfileNotFoundError。
    """

def resolve_session(hermes_home: Path, session_id: str | None) -> tuple[str, Path]:
    """解析 session_id → (session_id, db_path)。
    
    若 session_id 為 None，找到 {hermes_home}/sessions/ 下
    最近修改的 decohere.db。
    若找不到任何 session，拋出 NoSessionsError。
    """

def list_all_profile_sessions() -> list[dict]:
    """遍歷所有已知 profile，收集其 sessions。
    
    調用 `hermes profile list --json` 獲取 profile 列表，
    對每個 profile 調用 `hermes profile show <name> --json` 獲取路徑，
    掃描各 sessions 目錄。
    """

def open_db(db_path: Path, readonly: bool = True) -> sqlite3.Connection:
    """打開 decohere.db，應用 WAL + busy_timeout 設置。"""

def format_timestamp(ts: float) -> str:
    """unixepoch → '2026-05-09 20:15:03'"""

def parse_json_field(raw: str | None, default=None):
    """安全解析 entry_json 字串。損壞 JSON → default。"""

def audit_log(hermes_home: Path, entry: dict) -> None:
    """寫入一行 JSON 到 {hermes_home}/decohere_audit.log。"""

def format_turn_output(turn: dict, layer: str = 'full') -> str:
    """將 entry 字典格式化為終端輸出。"""

class DecohereCLIError(Exception):
    """所有 CLI 命令的基底異常。"""
class ProfileNotFoundError(DecohereCLIError):
    """指定的 profile 不存在。"""
class NoSessionsError(DecohereCLIError):
    """找不到任何 session。"""
```

#### DB 訪問模式

```
唯讀命令（sessions, list, show, search, export, stats）:
  conn = open_db(db_path, readonly=True)
  → 使用 URI file:...?mode=ro，不持有寫鎖

寫入命令（edit, delete）:
  conn = open_db(db_path, readonly=False)
  → WAL 模式，BEGIN IMMEDIATE 事務
  → 同一個 conn 內執行所有讀寫操作
  → 失敗時 ROLLBACK，成功時 COMMIT

連接生命週期:
  每個命令調用打開一個連接，命令結束時關閉。
  不復用跨命令連接（避免狀態殘留）。
```

---

### 4.3 測試基礎設施

#### conftest.py — DB Fixture

```python
@pytest.fixture
def temp_decohere_db(tmp_path):
    """創建預填充的 decohere.db，包含 5 個 turn 的假數據。
    
    Schema: 完整的 decohere schema（ledger_entries, concepts_fts, raw_messages, metadata）
    數據: 5 個有意義的 ledger entries，模擬典型的 hermes-agent 對話
    """
```

預填充的測試數據（5 turns）:

| Turn | Task | Tools | Concepts | Narrative |
|------|------|-------|----------|-----------|
| 1 | vision debug | vision_analyze | context window | Fixed vision truncation by raising max_tokens |
| 2 | config fix | patch, terminal | compression threshold | Set threshold to 0.35 in config.yaml |
| 3 | research | web_search, web_extract | Codex /goal, Ralph loop | Researched Codex /goal feature |
| 4 | code review | read_file, search_files | decohere, ledger entries | Reviewed decohere plugin architecture |
| 5 | refactor | patch, terminal | should_compress, placeholder | Fixed should_compress deadlock bug |

#### 每個命令的測試覆蓋

| 測試文件 | 最少測試數 | 關鍵場景 |
|---------|:-------:|---------|
| test_sessions.py | 5 | 有 sessions、無 sessions、--all-profiles、--profile 指定、profile 不存在 |
| test_list.py | 4 | 預設顯示、--limit、--offset、空 session |
| test_show.py | 5 | full 層級、l1 層級、l2 層級、不存在的 turn、損壞 JSON |
| test_search.py | 5 | FTS5 匹配、無匹配、布林查詢、--field narrative、跨 session |
| test_edit.py | 5 | 修改 user_intent、修改巢狀欄位、不存在的欄位、錯誤 JSON、rollback |
| test_delete.py | 4 | 單個刪除、批量刪除、不存在的 turn、--confirm |
| test_export.py | 3 | JSON 輸出、Markdown 輸出、寫入檔案 |
| test_stats.py | 4 | 正常統計、空 session、--json、pending entries 計數 |

合計最少 **35 個測試**（超過 30 的門檻）。

---

### 4.4 錯誤處理矩陣

| 錯誤類型 | 行為 | Exit Code | 訊息示例 |
|---------|------|:--------:|---------|
| 無 sessions | 表格顯示 "(no sessions found)" | 0 | N/A |
| session 無 entries | 表格顯示 "0 ledger entries" | 0 | N/A |
| FTS5 無匹配 | 顯示 "No results" | 0 | N/A |
| profile 不存在 | 錯誤 + 顯示可用 profile 列表 | 1 | `Profile 'foo' not found. Available: speak-off-the-cuff, default` |
| hermes_home 不存在 | 錯誤 | 1 | `Error: ~/.hermes does not exist` |
| session 不存在 | 錯誤 | 1 | `Error: session <id> not found` |
| turn 不存在 | 錯誤 | 1 | `Error: Turn N not found in session <id>` |
| DB 損壞 | 錯誤 | 1 | `Error: cannot open decohere.db: database disk image is malformed` |
| entry JSON 損壞 | 跳過該條目，繼續 | 0 | `[WARNING] Turn N: corrupted JSON, skipping` |
| 寫入權限不足 | 錯誤 | 1 | `Error: permission denied writing to <path>` |
| 用戶拒絕確認 | 中止操作 | 0 | `Aborted.` |
| edit field 不存在 | 錯誤 | 1 | `Error: field '<name>' not found in Turn N` |
| edit value JSON 無效 | 錯誤 | 1 | `Error: invalid JSON value` |
| 事務失敗 | ROLLBACK + 錯誤 | 1 | `Error: edit failed, changes rolled back` |

---

### 4.5 hermes_cli 註冊

在 `hermes_cli/cli.py` 或 `hermes_cli/__init__.py` 中添加：

```python
# 在頂層 Click 群組中註冊
from plugins.context_engine.decohere.cli import decohere_group
cli.add_command(decohere_group, name="decohere")
```

註冊後 `hermes decohere --help` 必須顯示所有子命令。測試會直接驗證這一點。

---

### 4.6 構建順序（依賴圖）

```
_shared.py  (第一步 — 所有命令的基礎)
    │
    ├── sessions_cmd.py  (第二步 — 獨立的，不依賴其他命令)
    │
    ├── list_cmd.py      (第三步 — 依賴 _shared)
    │
    ├── show_cmd.py      (第四步 — 依賴 _shared)
    │
    ├── search_cmd.py    (第五步 — 依賴 _shared)
    │
    ├── stats_cmd.py     (第六步 — 依賴 _shared + LedgerStore)
    │
    ├── export_cmd.py    (第七步 — 依賴 _shared + show 邏輯)
    │
    ├── edit_cmd.py      (第八步 — 依賴 _shared + validate_entry)
    │
    └── delete_cmd.py    (第九步 — 依賴 _shared + audit_log)
```

---

### 4.7 Codex /goal 執行考量

這個方案非常適合 `/goal`，因為：
- 每個步驟的輸出是**可測量的**（pytest exit code）
- 命令之間有清晰的依賴關係（`/goal` 可以自然按順序推進）
- 測試先行（test fixture 建好後，Codex 可以 TDD 每個命令）
- 失敗是可重試的（`/goal` 的軟停止機制可以從失敗點繼續）

`/goal` 可能會卡住的點（需要計劃中明確指導）：
- Click 命令組註冊的語法（給出代碼示例）
- 巢狀欄位路徑解析（`concepts_and_definitions[0].term` — 這需要遞迴字典訪問）
- FTS5 的 MATCH 語法與 SQLite 錯誤訊息（需要 try/except 包裹）

這些已在計劃的命令規格和錯誤矩陣中涵蓋。

### 方案 B：用 /goal 構建 Decohere 數據質量監控與自動修復

**目標：** 讓 Codex 構建一個持續監控系統，檢查 ledger entries 的完整性，
發現損壞/缺失/不一致時自動修復或報告。

```
/goal 構建 decohere 數據質量監控系統，包括：
1. 完整性檢查器（檢查每個 entry 的 9 個必須字段是否存在）
2. FTS5 索引一致性檢查器（檢查 concepts_fts 與 ledger_entries 同步）
3. 孤兒 raw_messages 檢測器（有 raw message 但無對應 ledger entry）
4. 自動修復引擎（重建 FTS5 索引、補齊缺失字段、標記損壞記錄）
5. 健康報告生成器（JSON + Markdown 雙格式輸出）

可測量的停止條件：
- `python -m plugins.context_engine.decohere.monitoring.health_check --session <test> --fix`
  對以下場景均產生正確結果：
  a) 正常 DB → 報告 "all checks passed"，exit code 0
  b) 缺失字段的 entry → 自動補齊默認值，exit code 0
  c) FTS5 不同步 → 自動重建，exit code 0
  d) 損壞 JSON → 標記並跳過，exit code 1 且報告列出損壞記錄
- 所有場景有對應的 pytest 測試（≥ 12 個）
- 健康報告 Markdown 文件包含：總 turns、損壞數、修復數、跳過數、每個檢查的通過/失敗狀態

Required workflow loop:
1. 閱讀 LedgerConfig、LedgerStore、驗證器的字段定義
2. 創建 plugins/context_engine/decohere/monitoring/health_check.py
3. 實現五個檢查器類，每個返回 (passed: bool, details: dict)
4. 實現修復引擎，可獨立調用每個修復函數
5. 在 tests/plugins/context_engine/decohere/monitoring/ 下為每個場景創建測試，
   使用預構造的損壞 SQLite DB fixture
6. 確保修復操作有事務包裹，失敗時回滾
7. 每完成一個檢查器+修復器對，運行對應測試確認通過

Hard constraints:
- 不修改現有 decohere 核心代碼
- 修復操作備份原始 entry_json 到 metadata 表（key = 'backup_turn_N'）
- 不向外部 API 發送數據
- 命令行接口：`python -m plugins.context_engine.decohere.monitoring.health_check [--session <id>] [--fix] [--output <path>]`

Stop signal: 所有場景測試通過（exit code 0），且至少 12 個測試

Hard cap: 120 分鐘 wall-clock
```

### 方案 C：用 /goal 構建跨 Session 的 Decohere 知識遷移引擎

**目標：** 讓 Codex 構建一個系統，能從多個 session 的 ledger entries 中提取持久知識，
寫入 shared 存儲，並在新 session 啟動時自動注入相關歷史知識。
這是 Decohere 從「per-session 記憶」升級到「跨 session 知識庫」的關鍵步驟。

```
/goal 構建 decohere 跨 session 知識遷移引擎，包括：
1. 知識提取器：掃描所有 session 的 concepts_and_definitions，
   合併重複概念，去重後寫入 shared 存儲
2. 相關性匹配器：給定當前 turn 的 user_intent，從 shared 存儲中
   檢索最相關的前 K 條歷史知識
3. 上下文注入器：在新 session 的上下文構建階段，自動注入匹配的歷史知識
4. Shared 存儲：在 <hermes_home>/decohere_shared.db 創建獨立的
   FTS5 索引數據庫，存儲跨 session 的持久概念

可測量的停止條件：
- 對至少 3 個不同的 session DB 運行遷移：
  `python -m plugins.context_engine.decohere.knowledge.migrate --all`
  成功提取概念，去重率 ≥ 30%（證明跨 session 有重疊）
- Shared DB 在遷移後有 ≥ 1 條記錄
- 給定一個測試查詢 "context window compression"，
  相關性匹配器返回 ≥ 1 條結果，且第一條的 turn_n 來自正確的原始 session
- 在新 session 啟動時，自動注入的知識以
  `## Shared Knowledge (from past sessions)` 為標題
  出現在上下文消息中
- 所有功能有 pytest 測試（≥ 15 個）
- `python -m pytest tests/plugins/context_engine/decohere/knowledge/ -x -q` 全部通過

Required workflow loop:
1. 閱讀所有現有 decohere 代碼，理解 SessionIO、LedgerStore、FTS5 使用方式
2. 創建 plugins/context_engine/decohere/knowledge/ 模塊：
   knowledge/
   ├── __init__.py
   ├── shared_store.py    # SharedStore 類，管理 decohere_shared.db
   ├── extractor.py       # 從多 session 提取並去重概念
   ├── matcher.py         # FTS5 相關性檢索
   ├── injector.py        # 上下文注入邏輯
   └── migrate.py         # CLI 入口：python -m ...knowledge.migrate
3. 在 __init__.py 的 compress() 中集成注入器：
   在 build_ledger_context 之後附加 shared knowledge 消息
4. 創建 tests/plugins/context_engine/decohere/knowledge/ 測試
5. 確保 shared DB 使用與 session DB 相同的連接安全模式
6. 測試去重邏輯：插入 10 條概念（其中 4 條重複），確認遷移後只有 6 條

Hard constraints:
- 不修改 LedgerStore、SessionIO 的核心接口
- Shared DB 不包含 raw_messages，只存儲概念和來源 session_id
- 遷移操作是唯讀的（只從 session DB 讀取，寫入 shared DB）
- 不向外部 API 發送任何 ledger 數據
- 注入的 shared knowledge 總量不超過當前上下文窗口的 10%

Stop signal: 所有知識模塊測試通過，且手動運行 migrate --all 成功提取並去重概念

Hard cap: 150 分鐘 wall-clock
```

---

## 五、方案優先級與依賴

| 順序 | 方案 | 依賴 | 產出 |
|------|------|------|------|
| 1 | **A** — CLI 工具鏈 | 無（純新增） | `hermes decohere list/show/search/edit/delete/export/stats/sessions` |
| 2 | **B** — 質量監控 | A（可用 CLI 手動驗證） | `health_check.py` + 自動修復 |
| 3 | **C** — 跨 Session 知識遷移 | A+B（CLI 可查詢，質量有保障） | `knowledge/` 模塊 + `decohere_shared.db` |

建議順序執行：先用 A 讓數據可見，再用 B 保證數據健康，最後用 C 讓數據跨 session 產生複利。

---

## 六、文件變更預覽（方案 A）

```
新增:
  plugins/context_engine/decohere/cli/__init__.py          # Click 命令組 + 註冊入口
  plugins/context_engine/decohere/cli/_shared.py           # resolve_session, open_db, audit_log 等共用工具
  plugins/context_engine/decohere/cli/sessions_cmd.py       # hermes decohere sessions
  plugins/context_engine/decohere/cli/list_cmd.py           # hermes decohere list
  plugins/context_engine/decohere/cli/show_cmd.py           # hermes decohere show
  plugins/context_engine/decohere/cli/search_cmd.py         # hermes decohere search（FTS5）
  plugins/context_engine/decohere/cli/edit_cmd.py           # hermes decohere edit（含巢狀欄位 + 審計日誌）
  plugins/context_engine/decohere/cli/delete_cmd.py         # hermes decohere delete（含批量模式）
  plugins/context_engine/decohere/cli/export_cmd.py         # hermes decohere export（json/md/yaml）
  plugins/context_engine/decohere/cli/stats_cmd.py          # hermes decohere stats
  tests/plugins/context_engine/decohere/cli/__init__.py
  tests/plugins/context_engine/decohere/cli/conftest.py     # temp_decohere_db fixture（5 turns 預填充）
  tests/plugins/context_engine/decohere/cli/test_sessions.py
  tests/plugins/context_engine/decohere/cli/test_list.py
  tests/plugins/context_engine/decohere/cli/test_show.py
  tests/plugins/context_engine/decohere/cli/test_search.py
  tests/plugins/context_engine/decohere/cli/test_edit.py
  tests/plugins/context_engine/decohere/cli/test_delete.py
  tests/plugins/context_engine/decohere/cli/test_export.py
  tests/plugins/context_engine/decohere/cli/test_stats.py

修改:
  hermes_cli/cli.py  # 註冊 `decohere` 命令組
```

---

## 七、風險與開放問題

| 風險 | 緩解 |
|------|------|
| `/goal` 在沒有測試基礎設施時無法自我驗證 | 方案 A 先建立測試 fixture，後續方案復用 |
| edit 操作可能破壞 JSON schema 完整性 | 重用現有 `validate_entry()` 進行寫後驗證 |
| 跨 session 知識遷移的隱私風險 | shared DB 只存概念不含 raw_messages 或用戶內容 |
| Codex `/goal` 可能消耗大量 token | 每個方案設 hard cap（90-150 分鐘 wall-clock） |

**開放問題：**
- `hermes decohere` 命令應註冊為頂層命令組還是 `hermes plugin decohere` 子命令？
- 是否需要 `hermes decohere vacuum`（清理孤兒記錄 + VACUUM）作為第八個子命令？
- Shared knowledge 注入是否需要用戶可見的開關？
