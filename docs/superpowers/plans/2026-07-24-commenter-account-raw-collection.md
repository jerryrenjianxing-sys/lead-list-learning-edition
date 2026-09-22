# Commenter Account Raw Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development while implementing each task.

**Goal:** Keep the original search and first/second-level comment collection unchanged, while optionally recording raw public commenter account data for later LLM analysis.

**Architecture:** Reuse the existing platform comment callbacks. Ordinary stores remain the source of complete comment output; `LeadSink` becomes a sidecar that records comments and fetches public profiles only for non-author comments containing at least one Chinese character, letter, or digit. Each API task gets its own output directory so old raw data cannot limit a new run.

**Tech Stack:** Python 3.11, FastAPI/Pydantic, pytest, React/TypeScript.

## Global Constraints

- Do not add dependencies, an intent classifier, keyword scoring, Excel export, or LLM calls.
- Do not fetch commenter posts; collect only comment data and public profile data.
- Empty, sticker-only, emoji-only, and symbol-only comments remain in ordinary comment output but do not trigger profile lookup.
- The existing 150-profile safety cap remains per task and never limits ordinary comments.
- Preserve unrelated uncommitted work in the current worktree.

---

### Task 1: Convert LeadSink to raw collection

**Files:**
- Modify: `tools/lead_sink.py`
- Test: `tests/test_lead_sink.py`

- [ ] Add a failing test proving a normal text comment creates a profile reference while empty and emoji/sticker-only comments do not.
- [ ] Add a failing test proving all raw comment records are written even after the former 300-comment limit is exhausted.
- [ ] Replace B2B keyword/nickname scoring with a small standard-library meaningful-text check.
- [ ] Keep every raw comment/reply record, deduplicate profile references by account ID, retain the 150-account cap, and store public IDs already present in comment payloads.
- [ ] Remove active profile/post analysis behavior and run `uv run pytest tests/test_lead_sink.py -q`.

### Task 2: Restore the native platform crawl flow

**Files:**
- Modify: `media_platform/douyin/core.py`
- Modify: `media_platform/xhs/core.py`
- Modify: `media_platform/kuaishou/core.py`
- Test: `tests/test_douyin_lead_collection.py`
- Test: `tests/test_xhs_lead_collection.py`
- Test: `tests/test_kuaishou_lead_collection.py`

- [ ] Add failing regression tests proving account collection does not replace normal search, does not cap ordinary comments, and respects the user's second-level comment setting.
- [ ] Always run each platform's existing normal search and concurrent comment path.
- [ ] Wrap the existing store callback to also write `LeadSink` raw records without changing ordinary maximums or stop conditions.
- [ ] Fetch public profiles once after crawling and remove commenter-post fetching.
- [ ] Run the three platform lead test modules.

### Task 3: Make the WebUI option independent and isolate each task

**Files:**
- Modify: `api/schemas/crawler.py`
- Modify: `api/services/crawler_manager.py`
- Modify: `webui/src/components/config/CrawlerConfigPanel.tsx`
- Modify: `webui/src/i18n/locales/zh-CN/config.json`
- Modify: `webui/src/i18n/locales/en-US/config.json`
- Test: `tests/test_api_limits.py`

- [ ] Add failing tests proving account collection does not force 10 contents, 300 comments, or second-level comments, and that it assigns an isolated task output path.
- [ ] Pass the user's original content/comment/subcomment settings unchanged.
- [ ] Auto-create `data/lead/<task-id>` as the save path when no explicit path is provided.
- [ ] Keep the three checkboxes independently usable and relabel the option as raw commenter account collection.
- [ ] Run `uv run pytest tests/test_api_limits.py -q` and `npm.cmd run build` in `webui`.

### Task 4: Full verification

- [ ] Run all affected backend tests together.
- [ ] Run the full backend test suite.
- [ ] Run the WebUI production build.
- [ ] Inspect the final diff for unrelated changes and confirm no new dependency or analysis/export code was added.
