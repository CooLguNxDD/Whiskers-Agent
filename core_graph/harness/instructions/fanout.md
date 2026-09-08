# Multi-fetch / fan-out (core)

When the user asks for multiple items ("all", "each", "first N", "both"):

1. List/search once, then fan out get-by-id over the results when needed.
2. Prefer a single list step plus bound consumers over N independent get calls with guessed ids.
