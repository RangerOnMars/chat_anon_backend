"""
Base abstractions for ASR, LLM, and TTS services
Provides common functionality for service lifecycle and configuration
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    """Base exception for all service errors"""
    pass


class LLMError(ServiceError):
    """Exception raised by LLM service"""
    pass


class TTSError(ServiceError):
    """Exception raised by TTS service"""
    pass


class ASRError(ServiceError):
    """Exception raised by ASR service"""
    pass


class ConnectionError(ServiceError):
    """Exception raised when a service connection fails"""
    pass


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
    tts_endpoint: str = "wss://api.minimaxi.com/ws/v1/t2a_v2"  # MiniMax TTS
    llm_endpoint: str = "https://ark.cn-beijing.volces.com/api/v3"
    
    # Resource IDs
    asr_resource_id: str = "volc.bigasr.sauc.duration"
    
    # Models
    llm_model: str = "doubao-seed-1-6-lite-251015"  # Unified default
    tts_model: str = "speech-2.8-hd"
    
    # Character configuration
    character_manifest_path: str = "prompts/anon/character_manifest.md"
    character_voice_id: str = "AnonTokyo2026012304"
    
    # Audio parameters
    asr_sample_rate: int = 16000
    tts_sample_rate: int = 16000
    audio_channels: int = 1
    audio_bits: int = 16
    audio_format: str = "pcm"  # Audio format for responses
    
    # Streaming parameters
    asr_segment_duration_ms: int = 200
    
    # LLM parameters
    llm_stream: bool = False
    llm_reasoning_effort: str = "low"
    
    # SSL/Security settings
    ssl_verify_tts: bool = False  # MiniMax may require disabled SSL verification
    
    # Timeout settings (in seconds)
    websocket_connect_timeout: float = 30.0
    websocket_receive_timeout: float = 0.01  # For non-blocking receives
    
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
    """
    Base class for streaming services (ASR, TTS) that maintain WebSocket connections.
    
    These services stream data bi-directionally over persistent connections,
    unlike LLMService which uses request/response over HTTP.
    """
    
    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        self.connection = None  # WebSocket connection
