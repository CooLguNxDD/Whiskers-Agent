"""Constants shared across the core_graph.node.helpers submodules.
Split out of core_graph/node/helpers.py (Phase 4 modularity refactor)."""
import re

# Common envelope keys that wrap a tool/HTTP result around its real payload.
# `validator_node` wraps bare results as {"status":"ok","data":{...}}, so a
# planner-guessed path like `$steps[0].id` must also be tried under `.data`.
_RESULT_ENVELOPE_KEYS = ("data", "result", "response", "item", "items", "messages", "records", "results", "data_sources")

_BINDING_RE = re.compile(r"^\$steps\[(\d+)\](?:\.(.+))?$")

_ID_ALIASES = {"id", "userid", "user_id", "_id"}

_MAX_DEEP_FIND_DEPTH = 50
