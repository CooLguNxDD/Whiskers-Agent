"""Telemetry utilities for extracting token usage and metrics from LLM responses."""

from typing import Any
import logging
logger = logging.getLogger("whiskers")

def extract_token_usage(response: Any) -> dict:
    """Extract token usage statistics from a LangChain v3 response.
    
    Tries response.usage_metadata, then response.response_metadata["token_usage"]
    and response.response_metadata["usage"].
    Returns {"input_tokens": int, "output_tokens": int, "total_tokens": int}.
    """
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0
    }
    
    if not response:
        return usage
        
    try:
        # 1. LangChain v3 primary metadata (AIMessage.usage_metadata)
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            meta = response.usage_metadata
            usage["input_tokens"] = meta.get("input_tokens", 0)
            usage["output_tokens"] = meta.get("output_tokens", 0)
            usage["total_tokens"] = meta.get("total_tokens", 0)
            return usage
            
        # 2. Fallback to response_metadata
        if hasattr(response, "response_metadata") and response.response_metadata:
            resp_meta = response.response_metadata
            
            token_usage = resp_meta.get("token_usage") or resp_meta.get("usage")
            if isinstance(token_usage, dict):
                usage["input_tokens"] = token_usage.get("prompt_tokens", token_usage.get("input_tokens", 0))
                usage["output_tokens"] = token_usage.get("completion_tokens", token_usage.get("output_tokens", 0))
                usage["total_tokens"] = token_usage.get("total_tokens", 0)
                
    except Exception:
        logger.debug("telemetry.py: swallowed exception", exc_info=True)
        
    return usage
