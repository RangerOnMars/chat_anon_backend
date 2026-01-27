"""
Base abstractions for ASR, LLM, and TTS services
Provides common functionality for service lifecycle and configuration
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ServiceConfig:
    """Configuration for backend services"""
    # Authentication (loaded from secrets.py)
    app_key: str
    access_key: str
    llm_api_key: str = None
    minimax_api_key: str = None
    
    # Service endpoints
    asr_endpoint: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    tts_endpoint: str = "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream"
    llm_endpoint: str = "https://ark.cn-beijing.volces.com/api/v3"
    
    # Resource IDs
    asr_resource_id: str = "volc.bigasr.sauc.duration"
    tts_resource_id: str = "seed-icl-2.0"
    
    # Models
    llm_model: str = "doubao-seed-1-8-251228"
    tts_model: str = "speech-2.8-hd"
    
    # Character configuration
    character_manifest_path: str = "prompts/anon/character_manifest.md"
    character_voice_id: str = "AnonTokyo2026012304"
    
    # Audio parameters
    asr_sample_rate: int = 16000
    tts_sample_rate: int = 16000
    audio_channels: int = 1
    audio_bits: int = 16
    
    # Streaming parameters
    asr_segment_duration_ms: int = 200
    
    # LLM parameters
    llm_stream: bool = False
    llm_reasoning_effort: str = "low"
    
    def __post_init__(self):
        """Validate configuration"""
        if not self.app_key or not self.access_key:
            raise ValueError("app_key and access_key are required")


class BaseService(ABC):
    """Abstract base class for all services (ASR, LLM, TTS)"""
    
    def __init__(self, config: ServiceConfig):
        self.config = config
        self._connected = False
        self._closed = False
    
    @abstractmethod
    async def connect(self):
        """Establish connection to service"""
        pass
    
    @abstractmethod
    async def disconnect(self):
        """Close connection to service"""
        pass
    
    @property
    def is_connected(self) -> bool:
        """Check if service is connected"""
        return self._connected and not self._closed
    
    async def __aenter__(self):
        """Context manager entry"""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        await self.disconnect()


class StreamingService(BaseService):
    """Base class for streaming services (ASR, TTS)"""
    
    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        self.connection = None
    
    @abstractmethod
    async def stream_data(self):
        """Stream data to/from service"""
        pass
