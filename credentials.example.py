"""
Credentials Configuration Template for ChatAnon Backend
========================================================
Copy this file to credentials.py and fill in your actual credentials.

WARNING: Never commit credentials.py to version control!

RECOMMENDED: Use environment variables instead of hardcoding credentials.
Set these environment variables:
- BYTEDANCE_APP_KEY
- BYTEDANCE_ACCESS_KEY
- DOUBAO_API_KEY
- MINIMAX_API_KEY
- VALID_API_TOKENS (comma-separated list, e.g., "token1,token2,token3")

The credentials.py file will use environment variables if set,
falling back to the values defined in the file.
"""
import os
from typing import List


def _get_env(key: str, default: str = "") -> str:
    """Get environment variable with fallback to default."""
    return os.environ.get(key, default)


def _get_env_list(key: str, default: List[str] = None) -> List[str]:
    """Get environment variable as list (comma-separated) with fallback."""
    env_value = os.environ.get(key)
    if env_value:
        return [token.strip() for token in env_value.split(",") if token.strip()]
    return default or []


# ByteDance Service Credentials (ASR/TTS)
BYTEDANCE_APP_KEY = _get_env("BYTEDANCE_APP_KEY", "your_bytedance_app_key")
BYTEDANCE_ACCESS_KEY = _get_env("BYTEDANCE_ACCESS_KEY", "your_bytedance_access_key")

# Doubao LLM API Key
DOUBAO_API_KEY = _get_env("DOUBAO_API_KEY", "your_doubao_api_key")

# MiniMax TTS API Key
MINIMAX_API_KEY = _get_env("MINIMAX_API_KEY", "your_minimax_api_key")

# Valid API Tokens for client authentication
# Each token allows one active connection at a time
_DEFAULT_TOKENS = [
    "your_token_1",
    "your_token_2",
]
VALID_API_TOKENS = _get_env_list("VALID_API_TOKENS", _DEFAULT_TOKENS)
