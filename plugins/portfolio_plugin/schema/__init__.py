"""Portfolio GenUI layout schema (mirrors CatPortfolio schema.ts).

Canonical home is ``plugins.portfolio_plugin.schema.ui_layout_schema``; this
package re-exports for shorter plugin-local imports.
"""

from plugins.portfolio_plugin.schema.ui_layout_schema import (  # noqa: F401
    BLOCK_TYPES,
    THEME_VAR_ALLOWLIST,
    UILayout,
    UILayoutMeta,
    UISource,
    filter_unknown_blocks,
    sanitize_theme_overrides,
    validate_layout,
)

__all__ = [
    "BLOCK_TYPES",
    "THEME_VAR_ALLOWLIST",
    "UILayout",
    "UILayoutMeta",
    "UISource",
    "filter_unknown_blocks",
    "sanitize_theme_overrides",
    "validate_layout",
]
