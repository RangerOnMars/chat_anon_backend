"""
Message Handlers - Centralized WebSocket message processing
Extracts message handling logic from main.py for better maintainability
"""
import asyncio
import base64
import logging
from typing import Dict, Optional, Callable, Awaitable, Any
from dataclasses import dataclass
from fastapi import WebSocket, WebSocketDisconnect

from server.character_manager import character_manager, CharacterManager
from server.connection_manager import connection_manager
from services import LLMError, TTSError, ASRError, ServiceError

logger = logging.getLogger(__name__)


# Type alias for message handler functions
MessageHandler = Callable[[WebSocket, str, Dict, Dict], Awaitable[Optional[bool]]]


@dataclass
class MessageContext:
    """Context passed to message handlers"""
    websocket: WebSocket
    token: str
    session: Dict
    data: Dict
    
    @property
    def config(self):
        """Get service config from session"""
        return self.session.get("config")
    
    @property
    def llm_service(self):
        """Get LLM service from session"""
        return self.session.get("llm_service")
    
    @property
    def tts_service(self):
        """Get TTS service from session"""
        return self.session.get("tts_service")
    
    @property
    def asr_service(self):
        """Get ASR service from session"""
        return self.session.get("asr_service")


def create_error_response(message: str) -> Dict:
    """Create a standardized error response"""
    return {"type": "error", "message": message}


async def send_error(websocket: WebSocket, message: str):
    """Send an error response to the client"""
    await websocket.send_json(create_error_response(message))


async def send_thinking(websocket: WebSocket, message: str = "Processing..."):
    """Send a thinking indicator to the client"""
    await websocket.send_json({"type": "thinking", "message": message})


async def send_status_event(websocket: WebSocket, event_type: str, **kwargs):
    """
    Send a status event to the client for pipeline stage tracking.
    
    Event types:
        - asr_start: ASR recognition started
        - asr_end: ASR recognition completed
        - llm_start: LLM inference started
        - llm_end: LLM inference completed
        - tts_start: TTS synthesis started for a segment
    """
    await websocket.send_json({"type": event_type, **kwargs})


def _make_silence_bytes(sample_rate: int, gap_seconds: float) -> bytes:
    """Generate PCM 16-bit mono silence. Length = sample_rate * 2 * gap_seconds bytes."""
    n_bytes = int(sample_rate * 2 * gap_seconds)
    return b"\x00" * n_bytes


async def _producer_tts_to_queue(
    tts_service,
    tts_text: str,
    emotion: Optional[str],
    queue: asyncio.Queue,
):
    """Run TTS dedicated stream and put chunks into queue; put None when done."""
    try:
        async for chunk in tts_service.synthesize_stream_dedicated(tts_text, emotion=emotion):
            await queue.put(chunk)
    except Exception as e:
        logger.error(f"TTS producer error: {e}")
    finally:
        await queue.put(None)


async def process_llm_and_stream_tts(
    ctx: MessageContext,
    user_input: str
) -> bool:
    """
    Common logic for processing user input through LLM and streaming TTS response.
    Multi-sentence: TTS all sentences in parallel; stream to client in order;
    send response for each sentence when that sentence starts playing; end with turn_end.
    """
    llm_service = ctx.llm_service
    tts_service = ctx.tts_service
    config = ctx.config
    
    await send_thinking(ctx.websocket)
    await send_status_event(ctx.websocket, "llm_start")
    
    try:
        response = await llm_service.chat(user_input)
    except LLMError as e:
        logger.error(f"LLM error: {e}")
        await send_error(ctx.websocket, f"LLM error: {e}")
        return False
    
    await send_status_event(
        ctx.websocket,
        "llm_end",
        elapsed_time=response.get("elapsed_time", 0),
    )
    
    raw_content = response["content"]
    cn_texts, jp_texts, emotion_labels = CharacterManager.parse_llm_response(raw_content)
    n = len(cn_texts)
    gap_seconds = getattr(config, "tts_sentence_gap_seconds", 0.5)
    silence_bytes = _make_silence_bytes(config.tts_sample_rate, gap_seconds)
    
    if n == 0:
        return True
    
    # One queue per segment; producers run in parallel
    queues = [asyncio.Queue() for _ in range(n)]
    tts_texts = [CharacterManager.convert_names_for_tts(jp) for jp in jp_texts]
    producers = [
        asyncio.create_task(
            _producer_tts_to_queue(tts_service, tts_texts[i], emotion_labels[i], queues[i])
        )
        for i in range(n)
    ]
    
    try:
        for i in range(n):
            # Wait for first chunk of this sentence (or None if failed)
            first = await queues[i].get()
            await send_status_event(
                ctx.websocket,
                "tts_start",
                text=tts_texts[i][:50] + "..." if len(tts_texts[i]) > 50 else tts_texts[i],
                emotion=emotion_labels[i],
            )
            # Send response for this sentence when it starts playing
            await ctx.websocket.send_json({
                "type": "response",
                "content_cn": cn_texts[i],
                "content_jp": jp_texts[i],
                "emotion": emotion_labels[i],
                "audio_format": config.audio_format,
                "audio_sample_rate": config.tts_sample_rate,
            })
            if first is not None:
                chunk_base64 = base64.b64encode(first).decode("utf-8")
                await ctx.websocket.send_json({
                    "type": "audio_chunk",
                    "audio_base64": chunk_base64,
                    "audio_format": config.audio_format,
                    "audio_sample_rate": config.tts_sample_rate,
                })
            while True:
                chunk = await queues[i].get()
                if chunk is None:
                    break
                chunk_base64 = base64.b64encode(chunk).decode("utf-8")
                await ctx.websocket.send_json({
                    "type": "audio_chunk",
                    "audio_base64": chunk_base64,
                    "audio_format": config.audio_format,
                    "audio_sample_rate": config.tts_sample_rate,
                })
            # Inter-sentence silence (except after last)
            if i < n - 1 and silence_bytes:
                chunk_size = 1024
                for off in range(0, len(silence_bytes), chunk_size):
                    part = silence_bytes[off : off + chunk_size]
                    chunk_base64 = base64.b64encode(part).decode("utf-8")
                    await ctx.websocket.send_json({
                        "type": "audio_chunk",
                        "audio_base64": chunk_base64,
                        "audio_format": config.audio_format,
                        "audio_sample_rate": config.tts_sample_rate,
                    })
        await ctx.websocket.send_json({"type": "audio_end"})
        await ctx.websocket.send_json({"type": "turn_end"})
    except Exception as e:
        logger.error(f"Error in process_llm_and_stream_tts: {e}")
        raise
    finally:
        for t in producers:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    
    return True


async def handle_text_message(ctx: MessageContext) -> Optional[bool]:
    """
    Handle a text chat message.
    
    Returns:
        None to continue, True to exit main loop
    """
    content = ctx.data.get("content", "").strip()
    if not content:
        await send_error(ctx.websocket, "Empty message content")
        return None
    
    await process_llm_and_stream_tts(ctx, content)
    connection_manager.increment_message_count(ctx.websocket)
    return None


async def handle_audio_message(ctx: MessageContext) -> Optional[bool]:
    """
    Handle a batch audio message (ASR -> LLM -> TTS).
    
    Returns:
        None to continue, True to exit main loop
    """
    audio_base64 = ctx.data.get("audio_base64", "")
    if not audio_base64:
        await send_error(ctx.websocket, "No audio data provided")
        return None
    
    asr_service = ctx.asr_service
    
    # Decode audio
    try:
        audio_data = base64.b64decode(audio_base64)
    except Exception as e:
        await send_error(ctx.websocket, f"Invalid audio data: {e}")
        return None
    
    # Signal ASR processing start
    await send_status_event(ctx.websocket, "asr_start")
    
    # Send thinking indicator
    await send_thinking(ctx.websocket, "Transcribing...")
    
    # Transcribe audio with ASR
    transcribed_texts = []
    try:
        async for text in asr_service.transcribe_audio(audio_data):
            transcribed_texts.append(text)
            await ctx.websocket.send_json({
                "type": "transcription",
                "text": text,
                "is_partial": True
            })
    except (ASRError, ServiceError) as e:
        logger.error(f"ASR error: {e}")
        await send_error(ctx.websocket, f"ASR error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected ASR error: {e}")
        await send_error(ctx.websocket, f"ASR error: {e}")
        return None
    finally:
        await asr_service.disconnect()
    
    if not transcribed_texts:
        await send_error(ctx.websocket, "No speech detected in audio")
        return None
    
    # Combine transcriptions and send final
    full_transcription = " ".join(transcribed_texts)
    
    # Signal ASR processing end
    await send_status_event(ctx.websocket, "asr_end", text=full_transcription)
    
    await ctx.websocket.send_json({
        "type": "transcription",
        "text": full_transcription,
        "is_partial": False
    })
    
    # Process through LLM and TTS
    await process_llm_and_stream_tts(ctx, full_transcription)
    connection_manager.increment_message_count(ctx.websocket)
    return None


async def handle_audio_stream_start(ctx: MessageContext) -> Optional[bool]:
    """Handle starting a streaming ASR session."""
    try:
        asr_service = ctx.asr_service
        await asr_service.start_streaming_session()
        
        # Signal ASR processing start
        await send_status_event(ctx.websocket, "asr_start")
        
        await ctx.websocket.send_json({
            "type": "audio_stream_started",
            "message": "Streaming ASR session started"
        })
        logger.info(f"ASR streaming started for token {ctx.token[:8]}...")
    except Exception as e:
        logger.error(f"Failed to start ASR streaming: {e}")
        await send_error(ctx.websocket, f"Failed to start ASR: {e}")
    return None


async def handle_audio_stream_chunk(ctx: MessageContext) -> Optional[bool]:
    """Handle receiving an audio chunk during streaming."""
    audio_base64 = ctx.data.get("audio_base64", "")
    if not audio_base64:
        return None
    
    try:
        audio_chunk = base64.b64decode(audio_base64)
        asr_service = ctx.asr_service
        
        # Send chunk to ASR
        await asr_service.send_audio_chunk(audio_chunk, is_last=False)
        
        # Try to receive any available transcriptions
        result = await asr_service.receive_transcription()
        if result and result.get("text"):
            await ctx.websocket.send_json({
                "type": "transcription",
                "text": result["text"],
                "is_partial": result.get("is_partial", True)
            })
    except Exception as e:
        logger.error(f"Error processing audio chunk: {e}")
    return None


async def handle_audio_stream_end(ctx: MessageContext) -> Optional[bool]:
    """Handle finishing a streaming ASR session and processing the result."""
    try:
        asr_service = ctx.asr_service
        
        # Send final empty chunk to signal end
        await asr_service.send_audio_chunk(b"", is_last=True)
        
        # Collect final transcriptions
        final_texts = await asr_service.finish_streaming_session()
        
        # Disconnect ASR
        await asr_service.disconnect()
        
        if not final_texts:
            await send_error(ctx.websocket, "No speech detected")
            return None
        
        # Combine transcriptions
        full_transcription = " ".join(final_texts)
        
        # Signal ASR processing end
        await send_status_event(ctx.websocket, "asr_end", text=full_transcription)
        
        # Send final transcription
        await ctx.websocket.send_json({
            "type": "transcription",
            "text": full_transcription,
            "is_partial": False
        })
        
        logger.info(f"ASR transcription: {full_transcription}")
        
        # Process through LLM and TTS
        await process_llm_and_stream_tts(ctx, full_transcription)
        connection_manager.increment_message_count(ctx.websocket)
        
    except Exception as e:
        logger.error(f"Error finishing ASR stream: {e}")
        await send_error(ctx.websocket, f"ASR error: {e}")
    return None


async def handle_switch_character(
    ctx: MessageContext,
    session_manager: Any
) -> Optional[bool]:
    """Handle character switch request."""
    new_character = ctx.data.get("character_name", "").lower()
    
    if not character_manager.is_character_available(new_character):
        await send_error(ctx.websocket, f"Character '{new_character}' not available")
        return None
    
    # Switch character in session
    if await session_manager.switch_character(ctx.token, new_character):
        await connection_manager.update_character(ctx.websocket, new_character)
        char_config = character_manager.get_character_config(new_character)
        
        await ctx.websocket.send_json({
            "type": "character_switched",
            "character": new_character,
            "character_display_name": char_config.display_name if char_config else new_character,
            "message": f"Switched to character: {new_character}"
        })
    else:
        await send_error(ctx.websocket, "Failed to switch character")
    return None


async def handle_clear_history(ctx: MessageContext) -> Optional[bool]:
    """Handle clearing conversation history."""
    ctx.llm_service.clear_history()
    await ctx.websocket.send_json({
        "type": "history_cleared",
        "message": "Conversation history cleared"
    })
    return None


async def handle_ping(ctx: MessageContext) -> Optional[bool]:
    """Handle keepalive ping."""
    await ctx.websocket.send_json({"type": "pong"})
    return None


async def handle_agent_mode(ctx: MessageContext) -> bool:
    """
    Handle continuous agent mode conversation.
    
    This mode enables hands-free voice interaction:
    - Client continuously streams audio chunks
    - Server-side VAD detects when user finishes speaking
    - Automatically processes through LLM + TTS
    - Signals when ready for next turn
    
    Returns:
        True if main loop should exit (connection closed), False otherwise
    """
    asr_service = ctx.asr_service
    config = ctx.config
    
    logger.info(f"Agent mode started for token {ctx.token[:8]}...")
    
    try:
        # Start ASR streaming session
        await asr_service.start_streaming_session()
        
        # Signal ASR processing start
        await send_status_event(ctx.websocket, "asr_start")
        
        # Signal client we're ready to listen
        await ctx.websocket.send_json({
            "type": "agent_listening",
            "message": "Ready to listen"
        })
        
        accumulated_text = ""
        is_processing = False
        
        while True:
            try:
                data = await asyncio.wait_for(
                    ctx.websocket.receive_json(),
                    timeout=config.websocket_receive_timeout
                )
                msg_type = data.get("type", "")
                
                if msg_type == "agent_mode_stop":
                    logger.info(f"Agent mode stopped by client for token {ctx.token[:8]}...")
                    break
                
                elif msg_type == "agent_audio_chunk" and not is_processing:
                    audio_base64 = data.get("audio_base64", "")
                    if not audio_base64:
                        continue
                    
                    try:
                        audio_chunk = base64.b64decode(audio_base64)
                        await asr_service.send_audio_chunk(audio_chunk, is_last=False)
                    except Exception as e:
                        logger.error(f"Error processing audio chunk: {e}")
                
                elif msg_type == "ping":
                    await ctx.websocket.send_json({"type": "pong"})
                    
            except asyncio.TimeoutError:
                pass
            
            # Check for transcription results (non-blocking)
            if not is_processing:
                result = await asr_service.receive_transcription()
                
                if result and result.get("text"):
                    text = result["text"]
                    is_partial = result.get("is_partial", True)
                    
                    # Send transcription to client
                    await ctx.websocket.send_json({
                        "type": "transcription",
                        "text": text,
                        "is_partial": is_partial
                    })
                    
                    # If this is a final (VAD-triggered) transcription, process it
                    if not is_partial:
                        is_processing = True
                        accumulated_text = text
                        logger.info(f"VAD detected speech end: {text}")
                        
                        # Signal ASR processing end
                        await send_status_event(ctx.websocket, "asr_end", text=accumulated_text)
                        
                        # Process through LLM and TTS (includes llm_start/llm_end/tts_start events)
                        await process_llm_and_stream_tts(ctx, accumulated_text)
                        
                        # Reset for next turn
                        accumulated_text = ""
                        is_processing = False
                        
                        # Restart ASR session for next turn
                        await asr_service.reset_for_next_turn()
                        
                        # Signal ASR processing start for new turn
                        await send_status_event(ctx.websocket, "asr_start")
                        
                        # Signal ready for next turn
                        await ctx.websocket.send_json({
                            "type": "agent_listening",
                            "message": "Ready to listen"
                        })
                        
                        connection_manager.increment_message_count(ctx.websocket)
                        
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected during agent mode for token {ctx.token[:8]}...")
        return True
    except Exception as e:
        logger.error(f"Error in agent mode: {e}", exc_info=True)
        try:
            await send_error(ctx.websocket, f"Agent mode error: {e}")
        except:
            return True
    finally:
        try:
            await asr_service.disconnect()
        except:
            pass
        logger.info(f"Agent mode ended for token {ctx.token[:8]}...")
    
    return False


class MessageRouter:
    """
    Routes incoming WebSocket messages to appropriate handlers.
    Provides a clean, extensible way to add new message types.
    """
    
    def __init__(self, session_manager: Any):
        self.session_manager = session_manager
        self._handlers: Dict[str, MessageHandler] = {}
        self._register_default_handlers()
    
    def _register_default_handlers(self):
        """Register all default message handlers"""
        self.register("message", self._wrap_handler(handle_text_message))
        self.register("audio_message", self._wrap_handler(handle_audio_message))
        self.register("audio_stream_start", self._wrap_handler(handle_audio_stream_start))
        self.register("audio_stream_chunk", self._wrap_handler(handle_audio_stream_chunk))
        self.register("audio_stream_end", self._wrap_handler(handle_audio_stream_end))
        self.register("clear_history", self._wrap_handler(handle_clear_history))
        self.register("ping", self._wrap_handler(handle_ping))
        # These handlers need special treatment
        self.register("switch_character", self._handle_switch_character)
        self.register("agent_mode_start", self._handle_agent_mode_start)
    
    def _wrap_handler(self, handler: Callable[[MessageContext], Awaitable[Optional[bool]]]):
        """Wrap a simple handler to match the MessageHandler signature"""
        async def wrapped(websocket: WebSocket, token: str, session: Dict, data: Dict) -> Optional[bool]:
            ctx = MessageContext(websocket=websocket, token=token, session=session, data=data)
            return await handler(ctx)
        return wrapped
    
    async def _handle_switch_character(
        self, websocket: WebSocket, token: str, session: Dict, data: Dict
    ) -> Optional[bool]:
        """Special handler for switch_character that needs session_manager"""
        ctx = MessageContext(websocket=websocket, token=token, session=session, data=data)
        return await handle_switch_character(ctx, self.session_manager)
    
    async def _handle_agent_mode_start(
        self, websocket: WebSocket, token: str, session: Dict, data: Dict
    ) -> Optional[bool]:
        """Special handler for agent_mode_start"""
        ctx = MessageContext(websocket=websocket, token=token, session=session, data=data)
        return await handle_agent_mode(ctx)
    
    def register(self, msg_type: str, handler: MessageHandler):
        """Register a message handler for a specific message type"""
        self._handlers[msg_type] = handler
        logger.debug(f"Registered handler for message type: {msg_type}")
    
    def unregister(self, msg_type: str):
        """Unregister a message handler"""
        if msg_type in self._handlers:
            del self._handlers[msg_type]
    
    async def route(
        self, 
        websocket: WebSocket, 
        token: str, 
        session: Dict, 
        data: Dict
    ) -> Optional[bool]:
        """
        Route a message to its handler.
        
        Args:
            websocket: The WebSocket connection
            token: API token
            session: Active session
            data: Message data
            
        Returns:
            None to continue, True to exit main loop, False for unknown message type
        """
        msg_type = data.get("type", "")
        
        handler = self._handlers.get(msg_type)
        if handler:
            return await handler(websocket, token, session, data)
        else:
            await send_error(websocket, f"Unknown message type: {msg_type}")
            return None
    
    @property
    def supported_message_types(self) -> list:
        """Get list of supported message types"""
        return list(self._handlers.keys())
