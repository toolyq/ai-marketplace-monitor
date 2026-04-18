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
