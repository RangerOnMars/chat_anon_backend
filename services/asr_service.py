"""
ASR Service - Speech-to-Text using ByteDance ASR API
Provides WebSocket-based streaming speech recognition
"""
import asyncio
import aiohttp
import struct
import gzip
import uuid
import json
import logging
from typing import AsyncGenerator, Optional, List

from services.base import StreamingService, ServiceConfig, ASRError

logger = logging.getLogger(__name__)


class ProtocolVersion:
    V1 = 0b0001


class MessageType:
    CLIENT_FULL_REQUEST = 0b0001
    CLIENT_AUDIO_ONLY_REQUEST = 0b0010
    SERVER_FULL_RESPONSE = 0b1001
    SERVER_ERROR_RESPONSE = 0b1111


class MessageTypeSpecificFlags:
    NO_SEQUENCE = 0b0000
    POS_SEQUENCE = 0b0001
    NEG_SEQUENCE = 0b0010
    NEG_WITH_SEQUENCE = 0b0011


class SerializationType:
    NO_SERIALIZATION = 0b0000
    JSON = 0b0001


class CompressionType:
    GZIP = 0b0001


class ASRService(StreamingService):
    """ASR Service for real-time speech recognition"""
    
    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        self.session: Optional[aiohttp.ClientSession] = None
        self.connection: Optional[aiohttp.ClientWebSocketResponse] = None
        self.seq = 1
        self.is_recording = False
        
        self.sample_rate = config.asr_sample_rate
        self.channels = config.audio_channels
        self.bits = config.audio_bits
        self.segment_duration_ms = config.asr_segment_duration_ms
        self.segment_size = self._calculate_segment_size()
    
    def _calculate_segment_size(self) -> int:
        """Calculate audio segment size in bytes"""
        size_per_sec = self.channels * (self.bits // 8) * self.sample_rate
        return size_per_sec * self.segment_duration_ms // 1000
    
    async def connect(self):
        """Establish WebSocket connection to ASR service"""
        if self._connected:
            return
        
        headers = {
            "X-Api-Resource-Id": self.config.asr_resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Access-Key": self.config.access_key,
            "X-Api-App-Key": self.config.app_key
        }
        
        try:
            self.session = aiohttp.ClientSession()
            self.connection = await self.session.ws_connect(
                self.config.asr_endpoint,
                headers=headers
            )
            self._connected = True
            logger.info(f"ASR service connected to {self.config.asr_endpoint}")
        except Exception as e:
            logger.error(f"Failed to connect to ASR service: {e}")
            if self.session:
                await self.session.close()
            raise
    
    async def disconnect(self):
        """Close WebSocket connection"""
        self.is_recording = False
        
        if self.connection:
            try:
                await self.connection.close()
            except Exception as e:
                logger.warning(f"Error closing ASR connection: {e}")
        
        if self.session:
            try:
                await self.session.close()
            except Exception as e:
                logger.warning(f"Error closing ASR session: {e}")
        
        self._connected = False
        self._closed = True
        logger.info("ASR service disconnected")
    
    def _build_header(self, message_type: int, flags: int) -> bytes:
        """Build protocol header"""
        header = bytearray()
        header.append((ProtocolVersion.V1 << 4) | 1)
        header.append((message_type << 4) | flags)
        header.append((SerializationType.JSON << 4) | CompressionType.GZIP)
        header.append(0x00)  # reserved
        return bytes(header)
    
    def _build_full_request(self) -> bytes:
        """Build initial full client request"""
        header = self._build_header(
            MessageType.CLIENT_FULL_REQUEST,
            MessageTypeSpecificFlags.POS_SEQUENCE
        )
        
        payload = {
            "user": {"uid": "server_uid"},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": self.sample_rate,
                "bits": self.bits,
                "channel": self.channels
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_nonstream": True
            }
        }
        
        payload_bytes = json.dumps(payload).encode('utf-8')
        compressed_payload = gzip.compress(payload_bytes)
        
        request = bytearray()
        request.extend(header)
        request.extend(struct.pack('>i', self.seq))
        request.extend(struct.pack('>I', len(compressed_payload)))
        request.extend(compressed_payload)
        
        return bytes(request)
    
    def _build_audio_request(self, audio_data: bytes, is_last: bool = False) -> bytes:
        """Build audio-only request"""
        seq = self.seq
        flags = MessageTypeSpecificFlags.POS_SEQUENCE
        
        if is_last:
            flags = MessageTypeSpecificFlags.NEG_WITH_SEQUENCE
            seq = -seq
        
        header = self._build_header(MessageType.CLIENT_AUDIO_ONLY_REQUEST, flags)
        compressed_data = gzip.compress(audio_data)
        
        request = bytearray()
        request.extend(header)
        request.extend(struct.pack('>i', seq))
        request.extend(struct.pack('>I', len(compressed_data)))
        request.extend(compressed_data)
        
        return bytes(request)
    
    def _parse_response(self, msg: bytes) -> dict:
        """Parse ASR response"""
        header_size = msg[0] & 0x0f
        message_type = msg[1] >> 4
        flags = msg[1] & 0x0f
        compression = msg[2] & 0x0f
        
        payload = msg[header_size*4:]
        
        is_last = bool(flags & 0x02)
        if flags & 0x01:
            payload = payload[4:]  # Skip sequence
        if flags & 0x04:
            payload = payload[4:]  # Skip event
        
        if message_type in [MessageType.SERVER_FULL_RESPONSE, MessageType.SERVER_ERROR_RESPONSE]:
            if message_type == MessageType.SERVER_ERROR_RESPONSE:
                payload = payload[4:]  # Skip error code
            payload_size = struct.unpack('>I', payload[:4])[0]
            payload = payload[4:]
        
        if compression == CompressionType.GZIP and payload:
            try:
                payload = gzip.decompress(payload)
            except Exception as e:
                logger.error(f"Failed to decompress: {e}")
                return {"is_last": is_last}
        
        result = {"is_last": is_last}
        if payload:
            try:
                result["data"] = json.loads(payload.decode('utf-8'))
            except Exception as e:
                logger.error(f"Failed to parse JSON: {e}")
        
        return result
    
    async def _send_initial_request(self):
        """Send initial configuration request"""
        request = self._build_full_request()
        await self.connection.send_bytes(request)
        self.seq += 1
        logger.debug(f"Sent initial ASR request with seq={self.seq-1}")
        
        msg = await self.connection.receive()
        if msg.type == aiohttp.WSMsgType.BINARY:
            response = self._parse_response(msg.data)
            logger.debug(f"Received initial response: {response}")
    
    async def transcribe_audio(self, audio_data: bytes) -> AsyncGenerator[str, None]:
        """
        Transcribe audio data and yield text results
        
        Args:
            audio_data: PCM audio data bytes
            
        Yields:
            str: Transcribed text from definite utterances
        """
        if not self.is_connected:
            await self.connect()
        
        self.seq = 1
        
        try:
            await self._send_initial_request()
            
            # Send audio in segments
            offset = 0
            while offset < len(audio_data):
                chunk = audio_data[offset:offset + self.segment_size]
                is_last = (offset + self.segment_size >= len(audio_data))
                
                request = self._build_audio_request(chunk, is_last=is_last)
                await self.connection.send_bytes(request)
                self.seq += 1
                
                offset += self.segment_size
                
                if not is_last:
                    await asyncio.sleep(self.segment_duration_ms / 1000)
            
            outputted_utterances = set()
            
            # Receive transcriptions
            async for msg in self.connection:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    response = self._parse_response(msg.data)
                    
                    if 'data' in response and isinstance(response['data'], dict):
                        result = response['data'].get('result', {})
                        utterances = result.get('utterances', [])
                        
                        for utterance in utterances:
                            if isinstance(utterance, dict):
                                is_definite = utterance.get('definite', False)
                                
                                if is_definite:
                                    text = utterance.get('text', '').strip()
                                    start_time = utterance.get('start_time', -1)
                                    end_time = utterance.get('end_time', -1)
                                    
                                    utterance_id = (start_time, end_time, text)
                                    if text and utterance_id not in outputted_utterances:
                                        outputted_utterances.add(utterance_id)
                                        yield text
                    
                    if response.get('is_last'):
                        break
                
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {msg.data}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket closed")
                    break
                    
        except Exception as e:
            logger.error(f"Error in ASR transcription: {e}")
            raise
    
    def stop_recording(self):
        """Stop recording"""
        self.is_recording = False
        logger.info("Stopping ASR recording")
    
    # ========== Streaming Session Methods ==========
    
    async def start_streaming_session(self):
        """
        Start a streaming ASR session.
        Call this before sending audio chunks.
        """
        if not self.is_connected:
            await self.connect()
        
        self.seq = 1
        self.is_recording = True
        self._outputted_utterances = set()
        
        await self._send_initial_request()
        logger.info("ASR streaming session started")
    
    async def send_audio_chunk(self, audio_chunk: bytes, is_last: bool = False):
        """
        Send a single audio chunk to the ASR service.
        
        Args:
            audio_chunk: PCM audio data bytes
            is_last: Whether this is the final chunk
        """
        if not self.is_connected or not self.connection:
            raise RuntimeError("ASR session not started. Call start_streaming_session() first.")
        
        request = self._build_audio_request(audio_chunk, is_last=is_last)
        await self.connection.send_bytes(request)
        self.seq += 1
        
        if is_last:
            self.is_recording = False
            logger.debug("Sent final audio chunk")
    
    async def receive_transcription(self) -> Optional[dict]:
        """
        Receive a single transcription response from the ASR service.
        
        Returns:
            dict with 'text' (if available), 'is_partial', and 'is_last' fields
            Returns None if connection is closed
        """
        if not self.is_connected or not self.connection:
            return None
        
        try:
            msg = await asyncio.wait_for(self.connection.receive(), timeout=0.01)
            
            if msg.type == aiohttp.WSMsgType.BINARY:
                response = self._parse_response(msg.data)
                result = {
                    "is_last": response.get("is_last", False),
                    "is_partial": True,
                    "text": None
                }
                
                if 'data' in response and isinstance(response['data'], dict):
                    data = response['data'].get('result', {})
                    utterances = data.get('utterances', [])
                    
                    texts = []
                    for utterance in utterances:
                        if isinstance(utterance, dict):
                            is_definite = utterance.get('definite', False)
                            text = utterance.get('text', '').strip()
                            
                            # Check for VAD trigger type (critical for detecting speech end)
                            additions = utterance.get('additions', {})
                            invoke_type = additions.get('invoke_type', '')
                            
                            if text:
                                # Only treat as final when BOTH definite AND hard_vad triggered
                                if is_definite and invoke_type == 'hard_vad':
                                    start_time = utterance.get('start_time', -1)
                                    end_time = utterance.get('end_time', -1)
                                    utterance_id = (start_time, end_time, text)
                                    
                                    if utterance_id not in self._outputted_utterances:
                                        self._outputted_utterances.add(utterance_id)
                                        texts.append(text)
                                        result["is_partial"] = False
                                        logger.debug(f"VAD hard_vad triggered: {text}")
                                else:
                                    # Partial transcription (not definite or not hard_vad)
                                    texts.append(text)
                    
                    if texts:
                        result["text"] = " ".join(texts)
                
                return result
            
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(f"WebSocket error: {msg.data}")
                return {"is_last": True, "is_partial": False, "text": None, "error": str(msg.data)}
            
            elif msg.type == aiohttp.WSMsgType.CLOSED:
                logger.info("WebSocket closed")
                return {"is_last": True, "is_partial": False, "text": None}
                
        except asyncio.TimeoutError:
            # No message available yet
            return {"is_last": False, "is_partial": True, "text": None}
        except Exception as e:
            logger.error(f"Error receiving transcription: {e}")
            return {"is_last": True, "is_partial": False, "text": None, "error": str(e)}
        
        return None
    
    async def finish_streaming_session(self) -> list:
        """
        Finish the streaming session and collect final transcriptions.
        
        Returns:
            List of final transcribed texts
        """
        final_texts = []
        
        # Keep receiving until we get the final response
        while True:
            result = await self.receive_transcription()
            
            if result is None:
                break
            
            if result.get("text") and not result.get("is_partial"):
                final_texts.append(result["text"])
            
            if result.get("is_last"):
                break
        
        logger.info(f"ASR streaming session finished with {len(final_texts)} transcriptions")
        return final_texts
    
    async def reset_for_next_turn(self):
        """
        Reset ASR session for next conversation turn.
        Disconnects current session and prepares for new streaming.
        Used in voice call mode for multi-turn conversations.
        """
        # Close current connection
        if self.connection:
            try:
                await self.connection.close()
            except Exception as e:
                logger.warning(f"Error closing ASR connection: {e}")
            self.connection = None
        
        if self.session:
            try:
                await self.session.close()
            except Exception as e:
                logger.warning(f"Error closing ASR session: {e}")
            self.session = None
        
        self._connected = False
        self._closed = False
        self.is_recording = False
        self.seq = 1
        self._outputted_utterances = set()
        
        # Reconnect and start new streaming session
        await self.connect()
        await self._send_initial_request()
        self.is_recording = True
        
        logger.info("ASR session reset for next turn")
