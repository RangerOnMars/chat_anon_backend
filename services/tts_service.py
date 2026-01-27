"""
TTS Service - Text-to-Speech using MiniMax TTS API
Provides WebSocket-based streaming text-to-speech synthesis
"""
import asyncio
import base64
import json
import logging
import ssl
import time
import websockets
from typing import Optional, AsyncGenerator, Callable

from services.base import StreamingService, ServiceConfig

logger = logging.getLogger(__name__)


class TTSService(StreamingService):
    """TTS Service for real-time text-to-speech synthesis using MiniMax"""
    
    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        self.connection: Optional[websockets.WebSocketClientProtocol] = None
        
        # MiniMax TTS configuration
        self.api_key = config.minimax_api_key
        self.model = config.tts_model
        self.voice_id = config.character_voice_id
    
    async def connect(self, force_reconnect=False):
        """Establish WebSocket connection to MiniMax TTS service"""
        if self._connected and not force_reconnect:
            return
        
        # Close existing connection if any
        if self.connection:
            try:
                await self.connection.close()
            except:
                pass
            self._connected = False
        
        url = "wss://api.minimaxi.com/ws/v1/t2a_v2"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        try:
            self.connection = await websockets.connect(
                url,
                additional_headers=headers,
                ssl=ssl_context,
                max_size=10 * 1024 * 1024
            )
            
            # Wait for connection success event
            connected = json.loads(await self.connection.recv())
            if connected.get("event") == "connected_success":
                self._connected = True
                logger.info("MiniMax TTS service connected")
            else:
                raise Exception(f"Connection failed: {connected}")
                
        except Exception as e:
            logger.error(f"Failed to connect to MiniMax TTS service: {e}")
            raise
    
    async def disconnect(self):
        """Close WebSocket connection"""
        if self.connection:
            try:
                await self.connection.send(json.dumps({"event": "task_finish"}))
                await self.connection.close()
            except Exception as e:
                logger.warning(f"Error closing TTS connection: {e}")
        
        self._connected = False
        logger.info("TTS service disconnected")
    
    async def _start_tts_task(self, emotion: Optional[str] = None):
        """
        Start TTS task with MiniMax
        
        Args:
            emotion: Optional emotion label for voice synthesis.
                     Valid values: happy, sad, angry, fearful, disgusted, surprised, calm, fluent, whisper
        """
        voice_setting = {
            "voice_id": self.voice_id,
            "speed": 0.95,
            "vol": 1.0,
            "pitch": 0,
            "english_normalization": False
        }
        
        # Add emotion to voice_setting if specified and not "auto"
        if emotion and emotion.lower() != "auto":
            voice_setting["emotion"] = emotion.lower()
            logger.info(f"TTS emotion set to: {emotion}")
        
        start_msg = {
            "event": "task_start",
            "model": self.model,
            "voice_setting": voice_setting,
            "audio_setting": {
                "sample_rate": 16000,
                "bitrate": 128000,
                "format": "pcm",
                "channel": 1
            },
            "pronunciation_dict": {
                "tone": ["あのん/Anon"],
            },
            "language_boost": "Japanese",
        }
        
        await self.connection.send(json.dumps(start_msg))
        response = json.loads(await self.connection.recv())
        
        if response.get("event") == "task_started":
            logger.info("TTS task started")
            return True
        else:
            logger.error(f"TTS task start failed: {response}")
            return False
    
    async def synthesize(
        self, 
        text: str, 
        emotion: Optional[str] = None,
        on_first_audio: Optional[Callable[[], None]] = None
    ) -> bytes:
        """
        Synthesize text to speech using MiniMax and return audio bytes
        
        Args:
            text: Text to synthesize
            emotion: Optional emotion label for voice synthesis.
            on_first_audio: Optional callback invoked when first audio chunk is received
            
        Returns:
            Audio data as bytes (PCM format)
        """
        # MiniMax requires a fresh connection for each synthesis task
        await self.connect(force_reconnect=True)
        
        send_time = time.time()
        
        try:
            if not await self._start_tts_task(emotion=emotion):
                raise Exception("Failed to start TTS task")
            
            # Send text for synthesis
            await self.connection.send(json.dumps({
                "event": "task_continue",
                "text": text
            }))
            logger.info(f"TTS request sent: {text[:50]}...")
            
            audio_buffer = bytearray()
            chunk_count = 0
            first_audio_received = False
            
            # Receive and process audio
            while True:
                response = json.loads(await self.connection.recv())
                
                # Handle audio data
                if "data" in response and "audio" in response["data"]:
                    audio_hex = response["data"]["audio"]
                    if audio_hex:
                        if not first_audio_received:
                            first_audio_time = time.time()
                            latency_ms = (first_audio_time - send_time) * 1000
                            logger.info(f"TTS first chunk latency: {latency_ms:.2f} ms")
                            first_audio_received = True
                            if on_first_audio:
                                on_first_audio()
                        
                        # Convert hex to bytes
                        audio_bytes = bytes.fromhex(audio_hex)
                        audio_buffer.extend(audio_bytes)
                        chunk_count += 1
                
                # Check if synthesis is complete
                if response.get("is_final"):
                    logger.info(f"TTS completed with {chunk_count} audio chunks, total {len(audio_buffer)} bytes")
                    break
            
            # Close connection after task completion
            await self.disconnect()
            
            return bytes(audio_buffer)
                
        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}")
            await self.disconnect()
            raise
    
    async def synthesize_stream(self, text: str, emotion: Optional[str] = None) -> AsyncGenerator[bytes, None]:
        """
        Synthesize text and yield audio chunks (for streaming to client)
        
        Args:
            text: Text to synthesize
            emotion: Optional emotion label
            
        Yields:
            bytes: Audio chunks (PCM format)
        """
        await self.connect(force_reconnect=True)
        
        try:
            if not await self._start_tts_task(emotion=emotion):
                raise Exception("Failed to start TTS task")
            
            await self.connection.send(json.dumps({
                "event": "task_continue",
                "text": text
            }))
            logger.info(f"TTS streaming request sent: {text[:50]}...")
            
            while True:
                response = json.loads(await self.connection.recv())
                
                if "data" in response and "audio" in response["data"]:
                    audio_hex = response["data"]["audio"]
                    if audio_hex:
                        audio_bytes = bytes.fromhex(audio_hex)
                        yield audio_bytes
                
                if response.get("is_final"):
                    logger.info("TTS streaming completed")
                    break
            
            await self.disconnect()
                    
        except Exception as e:
            logger.error(f"TTS streaming failed: {e}")
            await self.disconnect()
            raise
    
    async def stream_data(self):
        """Implementation of abstract method"""
        pass
