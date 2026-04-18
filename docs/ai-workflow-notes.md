# AI Workflow Notes

This file records the current AI-related behavior and configuration decisions for quick reference in future sessions.

## 1) Native behavior (without custom code changes)

### Overall flow
1. Marketplace results are fetched.
2. Rule-based filtering runs first (keywords, antikeywords, location, seller exclusions).
3. Items that pass are sent to AI for scoring.
4. Notification is sent only if AI score meets threshold.

### Key points
- Native project already supports AI scoring (1-5).
- Native `keywords` matching is string/logical-expression based, not semantic understanding.
- AI scoring is controlled by `item.rating` / `marketplace.rating` threshold.

## 2) What was added in this session

### New option
- `ai_keywords: bool` (default `false`)

### Behavior when `ai_keywords = true`
- Skip rule-based `keywords`/`antikeywords` blocking in listing pre-filter.
- Inject keyword criteria into AI prompt and ask model to semantically decide match quality.
- Keep using rating threshold for final pass/fail.

### Why this was added
- Better handling of seller wording variations, synonyms, and incomplete phrasing.
- Reduced misses for listings that are semantically correct but not exact keyword matches.

## 3) Required AI config reminders

For `provider = 'ollama'`, the following are required:
- `base_url`
- `api_key` (placeholder is acceptable for local OpenAI-compatible servers)
- `model` (must be a string)

Current local setup example:

```toml
[ai.lmstudio]
provider = 'ollama'
base_url = 'http://localhost:1234/v1'
api_key = 'lm-studio'
model = 'deepseek-r1:14b'
```

## 4) Current item strategy in config

For both target items:
- `ai_keywords = true`
- `ai = 'lmstudio'`
- `rating = 3`
- `description` and `extra_prompt` are used to constrain AI decision quality.

## 5) Practical tuning guidance

- If false positives are high: raise `rating` from `3` to `4`.
- If too many misses: keep `rating = 3`, improve `description` and `extra_prompt` with explicit must-have constraints.
- If latency/cost is a concern: disable `ai_keywords` and revert to rule-based keyword prefilter.

## 6) AI 评分缓存机制（中文速查）

### 是否会缓存
- 会。AI 对单个 listing 的评分结果会写入 diskcache，后续同样条件可直接命中缓存，避免重复调用模型。

### 缓存位置
- 缓存目录：项目根目录下的 `.ai-marketplace-monitor/`
- 初始化位置：`src/ai_marketplace_monitor/utils.py` 中 `cache = Cache(amm_home)`

### 缓存键（key）结构
- `(ai-inquiries, item_hash, marketplace_hash, listing_hash)`
- 只要 item 配置、marketplace 配置或 listing 内容哈希变化，就会视为新 key。

### 读写流程
1. `evaluate()` 先调用 `AIResponse.from_cache(...)` 读取。
2. 命中缓存则直接返回，不再请求 AI。
3. 未命中时调用模型。
4. 解析出 `score/comment` 后调用 `AIResponse.to_cache(...)` 写入。

### 不会写入缓存的情况
- AI 返回为空。
- 返回文本里无法解析出 `Rating` 分数格式（如缺少 `Rating <1-5>:`）。
- 这类情况会计入失败计数并抛出错误，不落盘缓存。

### 快速定位代码
- `src/ai_marketplace_monitor/ai.py`：`AIResponse.from_cache` / `AIResponse.to_cache` / `evaluate`
- `src/ai_marketplace_monitor/utils.py`：全局 `cache` 与 `CacheType.AI_INQUIRY`
