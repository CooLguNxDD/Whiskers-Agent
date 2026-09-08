---
name: web-search
description: Prefer web_search for open-internet research; use fetch_url only for known page URLs.
---

# Web search vs URL fetch

When the user asks to **search the web**, **look up online**, **google**, or
find facts/news/fan discussion on the open internet:

1. Call **`web_search`** with a focused `query` string.
2. Provider `auto` tries Tavily → Brave → **keyless DuckDuckGo**. No need to
   invent a search-engine URL.
3. Optionally call **`fetch_url`** on a **concrete result URL** from step 1 if
   full page text is needed.

**Do not** plan `fetch_url` of `google.com/search?...` or similar SERP URLs —
those pages are JS shells and return little useful text. **Do not** use GitHub
or Notion search tools for general web research.
