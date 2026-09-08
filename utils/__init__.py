"""Utility functions and configurations for Whiskers Agent Server."""

from .api_utils import (
    api_error_dict,
    safe_api_call,
    sanitize_body,
    sanitize_params,
    inject_pagination_defaults
)
from .json_processor import extract_by_keys, extract_lists, flatten_dict, resolve_array_string, safe_json_response, safe_text_response
from .response_format import (
    ResponseFormat,
    apply_format_async,
    to_csv,
    to_flat,
    to_id_list,
    to_summary,
)
from .response_shape import (
    apply_shape_async,
    apply_static_shape_async,
    build_meta_envelope,
    reload_shapes,
    strip_base64_fields,
)
from .server_config import (
    CONTEXT_CONFIG,
    MAX_RECOMMENDED_PAGE_SIZE,
    PAGINATION_CONFIG,
    SAFE_DEFAULT_PAGE_INDEX,
    SAFE_DEFAULT_PAGE_SIZE,
    SERVER_CONFIG,
)
from .tools_api_config import (
    ENDPOINT_META,
    RESPONSE_SHAPES,
    SETTINGS,
    TOOLS_CONFIG,
    get_endpoint_meta,
)

__all__ = [
    # api_utils
    "safe_api_call",
    "api_error_dict",
    "sanitize_params",
    "sanitize_body",
    "inject_pagination_defaults",
    # json_processor
    "extract_lists",
    "flatten_dict",
    "extract_by_keys",
    "resolve_array_string",
    "safe_json_response",
    "safe_text_response",
    # response_format
    "ResponseFormat",
    "apply_format_async",
    "to_csv",
    "to_id_list",
    "to_summary",
    "to_flat",
    # response_shape
    "apply_shape_async",
    "strip_base64_fields",
    "build_meta_envelope",
    "apply_static_shape_async",
    "reload_shapes",
    # server_config
    "SERVER_CONFIG",
    "PAGINATION_CONFIG",
    "CONTEXT_CONFIG",
    "SAFE_DEFAULT_PAGE_SIZE",
    "SAFE_DEFAULT_PAGE_INDEX",
    "MAX_RECOMMENDED_PAGE_SIZE",
    # tools_api_config
    "TOOLS_CONFIG",
    "SETTINGS",
    "RESPONSE_SHAPES",
    "ENDPOINT_META",
    "get_endpoint_meta",
]
