# Decohere 可勾選加載配置方案 v3

**日期:** 2026-05-11
**狀態:** 計劃階段（開放問題已決議，可執行）

---

## 目標

為 Decohere 提供用戶可控的跨 session 知識注入系統。架構設計支持即將到來的多模型管線和 Cross-Modal Retrieval。

---

## 核心設計決策

### 多模型管線

```
DeepSeek   → 生成 ledger entries  → 存入 session decohere.db
GPT        → 壓縮                  → 選取要保留的 turns
Gemini     → 檢索                  → 從 shared_store 匹配相關概念
未來模型    → 注入                  → 將匹配結果寫入上下文
```

對應的生成路徑 **不存 embedding**，檢索時即時計算。

### Embedding-agnostic 架構

- **`shared_store`**：只存文本。`(term, definition, source_session, source_turn)`。無向量列。
- **`matcher`**：接受 `embed_fn: str -> list[float]` 參數。不知道、不關心當前用的是哪個模型。
- **`injector`**：持有 `embed_fn`，傳給 `matcher`。`embed_fn` 由配置決定（`decohere.retrieval.model: "gemini-embedding"`）。
- **`compress()`**：只決定**何時觸發**注入。不關心檢索細節。

### 為什麼不存 embedding

| 原因 | 解釋 |
|------|------|
| 多模型管線 | DeepSeek 的 embedding ≠ Gemini 的 embedding。存了就鎖死了模型選擇。 |
| 概念演化 | 同一條概念在 session 3 和 session 15 的上下文不同。即時計算可以 incorporate 上下文。 |
| Cross-Modal 未來 | 圖像 → 文本檢索需要不同的 embedding model。預存向量只能是文本的，阻斷擴展。 |
| Cache 層可選 | 如果將來需要加速，可以在 `matcher` 內部加 LRU cache，但這是優化，不是架構。 |

### Cross-Modal 擴展路徑

```
matcher.retrieve(query, embed_fn, top_k=10)
         ↑
         ├── 文本檢索:  embed_fn = text_embedding_model("gemini-embedding")
         ├── 圖像檢索:  embed_fn = image_embedding_model("clip-vit")
         └── 音頻檢索:  embed_fn = audio_embedding_model("whisper-embed")
```

`shared_store` 本身不變。`matcher` 接口不變。唯一變化是調用端傳入的 `embed_fn` 和 `query` 格式。

---

## 方案設計

### 一、shared_store 架構

```sql
-- decohere_shared.db @ HERMES_HOME/

CREATE TABLE shared_concepts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    definition TEXT NOT NULL,
    source_session TEXT NOT NULL,
    source_turn INTEGER,
    imported_by TEXT DEFAULT 'user',     -- 'user' | 'auto' | 'migrate'
    imported_at REAL DEFAULT (unixepoch('subsec'))
);

CREATE VIRTUAL TABLE shared_concepts_fts
    USING fts5(term, definition, content='');

CREATE TABLE import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_session TEXT NOT NULL,
    source_turn INTEGER,
    action TEXT NOT NULL,               -- 'import' | 'exclude' | 'remove'
    timestamp REAL DEFAULT (unixepoch('subsec'))
);
```

### 二、Module API

```python
# shared_store.py

class SharedStore:
    def __init__(self, hermes_home: Path):
        """Open or create decohere_shared.db at hermes_home."""

    def add_concept(self, term: str, definition: str,
                    source_session: str, source_turn: int) -> int:
        """Insert or skip if duplicate (case-insensitive term + same session)."""

    def get_all(self) -> list[dict]:
        """Return all concepts."""

    def remove_by_source(self, source_session: str) -> int:
        """Remove all concepts from a session. Returns count."""

    def search_text(self, query: str, limit: int = 20) -> list[dict]:
        """FTS5 text search."""


# matcher.py

def retrieve_semantic(
    store: SharedStore,
    query: str,
    embed_fn: Callable[[str], list[float]],
    top_k: int = 10,
) -> list[dict]:
    """Compute embedding of query, cosine-similarity against all concepts,
    return top_k results.  embed_fn is model-agnostic."""

def retrieve_text(
    store: SharedStore,
    query: str,
    limit: int = 10,
) -> list[dict]:
    """FTS5 text-based retrieval. Zero-cost baseline."""


# injector.py

def build_injection_message(
    store: SharedStore,
    user_config: DecohereUserConfig,
    user_intent: str,
    embed_fn: Callable | None = None,
) -> dict | None:
    """Build the '## Shared Knowledge' message block.
    
    If embed_fn is provided, use semantic retrieval.
    Otherwise fall back to text-based FTS5.
    
    Returns a message dict or None if no knowledge to inject.
    """
```

### 三、配置

```yaml
# config.yaml

decohere:
  knowledge_injection: false

  retrieval:
    mode: "text"                      # "text" | "semantic"
    semantic_model: null              # 後續: "gemini-embedding", "openai-embedding-3"
    semantic_threshold: 0.75          # 後續: 低於此分數的不注入
    top_k: 10

  knowledge_sources:
    - session: "abc123"
      turns: [3, 5]

  knowledge_exclude:
    - "context window"

  injection:
    max_tokens_pct: 0.10
    max_concepts: 20
```

配置設計原則：
- `retrieval.mode: "text"` 今天就能跑（零外部依賴的 FTS5）
- `retrieval.mode: "semantic"` 是未來選項，需要 `semantic_model` 和 `embed_fn`
- 切換模式只需改一行 config，不需要改代碼
- `knowledge_exclude` 對兩種模式都生效

### 四、實現步驟

| # | 步驟 | 文件 | 產出 |
|---|------|------|------|
| 1 | 配置層 | `config.py` | `DecohereUserConfig` dataclass + load/save |
| 2 | SharedStore | `knowledge/shared_store.py` | CRUD, FTS5, import_log |
| 3 | Text Matcher | `knowledge/matcher.py` | `retrieve_text()` — FTS5 baseline |
| 4 | Injector | `knowledge/injector.py` | 過濾 + 構建消息塊 |
| 5 | CLI | `cli/knowledge_cmd.py` | sources, toggle, select, deselect, exclude, config |
| 6 | 集成 | `__init__.py` | compress() 掛載 injector |

步驟 3 的 `retrieve_semantic` 函數簽名已定義但實現留空（`raise NotImplementedError`），
後續任何時候接入 embedding model 只需實現它，不需要改其他文件。

### 五、注入格式

```
## Shared Knowledge (from past sessions)

Concepts from previous sessions, imported by user selection:

• Codex /goal: OpenAI Codex CLI autonomous task execution loop
  [session abc123, turn 3]

• Ralph loop: Verification loop pattern — Codex self-checks output
  [session abc123, turn 3]

Source: 2 sessions, 2 concepts. Injected per user configuration.
```

消息類型：`{"role": "user", "name": "shared_knowledge", "content": "..."}`
插入位置：compress() 返回的 L1/L2 消息之後。

### 六、測試

| 文件 | 數量 | 場景 |
|------|:---:|------|
| `test_knowledge_cmd.py` | 8 | sources, toggle on/off, select/deselect, exclude, config, json |
| `test_shared_store.py` | 4 | insert, dedup, remove_by_source, search_text |
| `test_matcher.py` | 3 | text retrieval, text no match, semantic signature (NotImplementedError) |
| `test_injector.py` | 4 | enabled, disabled, source filter, exclude filter |
| `test_config_persistence.py` | 4 | save, load, roundtrip, defaults |

合計 **≥23 個測試**。

### 七、文件變更

```
新增:
  plugins/context_engine/decohere/knowledge/__init__.py
  plugins/context_engine/decohere/knowledge/shared_store.py
  plugins/context_engine/decohere/knowledge/matcher.py
  plugins/context_engine/decohere/knowledge/injector.py
  plugins/context_engine/decohere/cli/knowledge_cmd.py

修改:
  plugins/context_engine/decohere/config.py
  plugins/context_engine/decohere/__init__.py
  plugins/context_engine/decohere/cli/__init__.py

測試新增:
  tests/plugins/context_engine/decohere/cli/test_knowledge_cmd.py
  tests/plugins/context_engine/decohere/knowledge/conftest.py
  tests/plugins/context_engine/decohere/knowledge/test_shared_store.py
  tests/plugins/context_engine/decohere/knowledge/test_matcher.py
  tests/plugins/context_engine/decohere/knowledge/test_injector.py
  tests/plugins/context_engine/decohere/knowledge/test_config_persistence.py
```
