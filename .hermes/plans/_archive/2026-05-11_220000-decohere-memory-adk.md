# Decohere 記憶架構分析 — 對標 Google ADK 三層記憶模式

## ADK 三層記憶 vs Decohere 現狀

| 層級 | ADK 模式 | 存儲 | 生命週期 | Decohere 對應 | 差距 |
|------|---------|------|---------|-------------|------|
| L1 會話記憶 | Session object + Event 記錄 | 內存 (InMemory) | 單次會話 | `SessionIO` + `decohere.db` (SQLite WAL) | ✅ 已超越：持久化到磁盤，crash 不丟 |
| L2 工作記憶 | `session.state` dict + `state_delta` | 內存 key-value | 單次會話內跨 agent | `shared_store` + `knowledge_injection` | ⚠️ 有基礎，缺多 agent 寫入 |
| L3 持久記憶 | `DatabaseSessionService` (SQLite/PG) | 磁盤 | 跨會話，月級 | `decohere_shared.db` + `SharedStore` | ⚠️ 有基礎，缺自動化和偏好提取 |

## 逐層分析

### L1 會話記憶 — Decohere 已超越

ADK 的 `InMemorySessionService` 默認只存內存，crash 就丟。要持久化需手動切 `DatabaseSessionService`。

Decohere **天生持久**：每 session 一個 `decohere.db`，WAL 模式，毫秒級寫入。不但存原始消息（`raw_messages`），還存 LLM 壓縮過的結構化摘要（`ledger_entries` + FTS5 索引）。這是 ADK 沒有的—— ADK 存的是原始 Event 流，Decohere 存的是**經 LLM 精煉的知識圖譜**。

**優化空間：** Session metadata 增強。目前 session 目錄名只是一個 ID，沒有標題/標籤/摘要。加一個 `sessions.json` 索引文件或 metadata 表可加速瀏覽。

### L2 工作記憶 — Decohere 的 shared_store 是雛形

ADK 的 `state` 是內存 dict，多 agent 通過 `state_delta` 協作。Decohere 沒有內存 dict，但有 `shared_store`（SQLite）。區別在於：

| | ADK state | Decohere shared_store |
|---|---|---|
| 寫入者 | 任意 agent 通過 `state_delta` | 用戶手動 `knowledge select` |
| 讀取 | agent 直接讀 `session.state` | `injector` 在 compress 時注入上下文 |
| 粒度 | 任意 key-value | 結構化 `(term, definition)` |
| 持久 | 否（除非 DatabaseSessionService） | 是（SQLite） |

**差距：** ADK 的 state 是多 agent **主動讀寫**的工作區。Decohere 的 shared_store 是**被動導入**的知識庫。缺的是：
- **自動寫入**：LLM posting 完成後自動將新概念推入 shared_store
- **多 agent 寫入**：subagent 可以往 shared_store 寫階段性發現
- **作用域隔離**：`user:` vs `app:` prefix，類似 ADK 的 state scope

### L3 持久記憶 — 最大差距

ADK 的 `DatabaseSessionService` 能做到「幾天甚至幾個月前的對話和偏好」自動恢復。

Decohere 的 `shared_store` 做到了跨 session 概念存儲，但缺三樣關鍵能力：

| 能力 | ADK | Decohere | 差距 |
|------|-----|---------|------|
| 偏好提取 | 隱含在 session state | 無 | 需從多 session 的 `user_intent` 和 `decisions` 中提取模式 |
| 自動導入 | session 重啟自動載入 | 用戶手動 `knowledge select` | 需基於頻率/時效的自動選擇策略 |
| 時效衰減 | 無（ADK 也沒做） | 無 | 舊概念應降低權重，長期不用應清理 |

---

## 優化路線圖

### Phase 1：補齊傷口（立即可做）
- Posting 失敗報錯已修（`task_manager.py` 的 `except Exception` 現在帶 `exc_info=True`）
- `health_check --fix` 可補齊缺失字段的 placeholder

### Phase 2：L2 增強 — 自動導入（1-2 小時）
```
post_entry() 完成 → validate_entry() → 寫入 ledger_entries
    ↓ 新增
extract_concepts() → SharedStore.add_concept(auto) → 更新 import_log
```

用戶不再需要手動 `knowledge select`。每次 LLM posting 成功，自動將新概念寫入 shared_store。`imported_by: "auto"` 標記區分自動和手動導入。

### Phase 3：L3 增強 — 偏好提取（2-3 小時）
```python
def extract_preferences(store: SharedStore) -> list[Preference]:
    """從 user_intent + decisions 欄位中提取用戶偏好模式。
    
    啟發式：
    - user_intent 反覆出現的動詞短語 → topic preference
    - decisions 中頻繁選取的選項 → tool/approach preference
    - critical_reflection.improvement_directions 中出現的 → pain points
    """
```

偏好存儲在同一 `decohere_shared.db`，`preferences` 表。

### Phase 4：L2 增強 — 時效評分（1 小時）
```sql
ALTER TABLE shared_concepts ADD COLUMN score REAL DEFAULT 1.0;
ALTER TABLE shared_concepts ADD COLUMN last_accessed REAL;
```

`score` 隨時間衰減（older → lower），`last_accessed` 在檢索命中時更新。`vacuum` 命令可清理 `score < 0.1` 的概念。

### Phase 5：L2 增強 — 多 Agent 寫入（待定）
給 `delegate_task` 的子 agent 開放 `shared_store` 寫入權限，通過 `state_delta` 模式：
```python
# subagent 回傳
{"shared_store_write": [
    {"term": "discovery_X", "definition": "Found during sub-task", "scope": "session"}
]}
```

---

## 結論

Decohere 在 **L1（會話記憶）** 上已超越 ADK — 持久化 + LLM 精煉是 ADK 沒有的。**L2/L3** 差距不在架構（`shared_store` 架構已完備），而在自動化程度 — 目前依賴手動操作。上面四步可把 Decohere 從「手動知識庫」升級為「自動記憶系統」。
