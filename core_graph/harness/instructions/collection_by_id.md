# Collection → by-id chaining (core)

When the user wants a specific resource (first session, a page, one item) but only has a free-text hint (name, "first", "latest") and **not** a real opaque id:

1. **List or search first** — call the collection op (`list_*` / `search_*` / `find_*`) with no fabricated id.
2. **Then get by id** — call the by-id op (`get_*` / `fetch_*` / `read_*`) with `*Id` / `*_id` bound from the list step (`$steps[0].id` or the field the list returns).
3. **Never invent ids** — do not put words from the user query (e.g. "Jules", "first") into `sessionId` / `pageId` / `id`.
4. **Order matters** — collection producer must run **before** the by-id consumer; the consumer must `depends_on` the producer.

Applies to plugins such as Jules (`juleslist_sessions` then `julesget_session`), Notion pages, and similar REST resources.
