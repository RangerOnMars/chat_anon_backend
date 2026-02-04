"""
Server Configuration - Loads configuration from secrets and config files
"""
import os
import logging
import yaml
from typing import Optional
from dataclasses import dataclass

from services.base import ServiceConfig

logger = logging.getLogger(__name__)

# Base directory
BASE_DIR = os.path.dirname(os.path.dirname(__file__))


def load_secrets():
    """Load secrets from credentials.py"""
    try:
        from credentials import (
            BYTEDANCE_APP_KEY,
            BYTEDANCE_ACCESS_KEY,
            DOUBAO_API_KEY,
            MINIMAX_API_KEY,
            VALID_API_TOKENS
        )
        return {
            "app_key": BYTEDANCE_APP_KEY,
            "access_key": BYTEDANCE_ACCESS_KEY,
            "llm_api_key": DOUBAO_API_KEY,
            "minimax_api_key": MINIMAX_API_KEY,
            "valid_tokens": VALID_API_TOKENS
        }
    except ImportError as e:
        logger.error(f"Failed to import credentials: {e}")
        logger.error("Please copy credentials.example.py to credentials.py and fill in your credentials")
        raise


def load_yaml_config(config_path: Optional[str] = None) -> dict:
    """Load configuration from YAML file"""
    if config_path is None:
        config_path = os.path.join(BASE_DIR, "config.yaml")
    
    if not os.path.exists(config_path):
        logger.warning(f"Config file not found: {config_path}, using defaults")
        return {}
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return {}


@dataclass
class ServerConfig:
    """Server configuration"""
    host: str = "0.0.0.0"
    port: int = 8765
    debug: bool = False
    log_level: str = "INFO"
    ssl_certfile: Optional[str] = None
    ssl_keyfile: Optional[str] = None


def create_service_config(character_name: str = "anon") -> ServiceConfig:
    """
    Create a ServiceConfig instance with secrets loaded.
    
    Args:
        character_name: Name of the character to use
        
    Returns:
        ServiceConfig instance
    """
    secrets = load_secrets()
    yaml_config = load_yaml_config()
    
    # Get character-specific settings
    from server.character_manager import character_manager
    char_config = character_manager.get_character_config(character_name)
    
    if not char_config:
        logger.warning(f"Character '{character_name}' not found, using 'anon'")
        char_config = character_manager.get_character_config("anon")
    
    # Per-character overrides from config.yaml (tts_model, voice_id)
    char_overrides = yaml_config.get("characters", {}).get(character_name, {})
    tts_model = char_overrides.get("tts_model") or yaml_config.get("models", {}).get(
        "tts_model", "speech-2.8-hd"
    )
    character_voice_id = char_overrides.get("voice_id") or (
        char_config.voice_id if char_config else "AnonTokyo2026012304"
    )
    
    # Merge configurations
    return ServiceConfig(
        # Credentials from secrets
        app_key=secrets["app_key"],
        access_key=secrets["access_key"],
        llm_api_key=secrets["llm_api_key"],
        minimax_api_key=secrets["minimax_api_key"],
        
        # Service endpoints (can be overridden in yaml)
        asr_endpoint=yaml_config.get("endpoints", {}).get(
            "asr", "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
        ),
        tts_endpoint=yaml_config.get("endpoints", {}).get(
            "tts", "wss://api.minimaxi.com/ws/v1/t2a_v2"
        ),
        llm_endpoint=yaml_config.get("endpoints", {}).get(
            "llm", "https://ark.cn-beijing.volces.com/api/v3"
        ),
        
        # Resource IDs
        asr_resource_id=yaml_config.get("resources", {}).get(
            "asr_resource_id", "volc.bigasr.sauc.duration"
        ),
        
        # Models
        llm_model=yaml_config.get("models", {}).get(
            "llm_model", "doubao-seed-1-6-lite-251015"
        ),
        tts_model=tts_model,
        
        # Character configuration
        character_manifest_path=char_config.manifest_path if char_config else "prompts/anon/character_manifest.md",
        character_voice_id=character_voice_id,
        
        # Audio parameters
        asr_sample_rate=yaml_config.get("audio", {}).get("asr_sample_rate", 16000),
        tts_sample_rate=yaml_config.get("audio", {}).get("tts_sample_rate", 16000),
        audio_channels=yaml_config.get("audio", {}).get("channels", 1),
        audio_bits=yaml_config.get("audio", {}).get("bits", 16),
        audio_format=yaml_config.get("audio", {}).get("format", "pcm"),
        
        # Streaming parameters
        asr_segment_duration_ms=yaml_config.get("audio", {}).get("asr_segment_duration_ms", 200),
        tts_sentence_gap_seconds=yaml_config.get("audio", {}).get("tts_sentence_gap_seconds", 0.5),
        
        # LLM parameters
        llm_stream=yaml_config.get("llm", {}).get("stream", False),
        llm_reasoning_effort=yaml_config.get("llm", {}).get("reasoning_effort", "low"),
        
        # SSL/Security settings
        ssl_verify_tts=yaml_config.get("ssl", {}).get("verify_tts", False),
        
        # Timeout settings
        websocket_connect_timeout=yaml_config.get("timeouts", {}).get("websocket_connect", 30.0),
        websocket_receive_timeout=yaml_config.get("timeouts", {}).get("websocket_receive", 0.01),
    )


def get_server_config() -> ServerConfig:
    """Get server configuration"""
    yaml_config = load_yaml_config()
    server_section = yaml_config.get("server", {})
    ssl_section = yaml_config.get("ssl", {})
    
    return ServerConfig(
        host=server_section.get("host", "0.0.0.0"),
        port=server_section.get("port", 8765),
        debug=server_section.get("debug", False),
        log_level=yaml_config.get("logging", {}).get("level", "INFO"),
        ssl_certfile=ssl_section.get("ssl_certfile"),
        ssl_keyfile=ssl_section.get("ssl_keyfile"),
    )


def get_timeout_config() -> dict:
    """
    Get timeout configuration from yaml.
    Returns a dict with timeout settings for use before session creation.
    """
    yaml_config = load_yaml_config()
    timeouts = yaml_config.get("timeouts", {})
    
    return {
        "websocket_connect": timeouts.get("websocket_connect", 30.0),
        "websocket_receive": timeouts.get("websocket_receive", 0.01),
    }
