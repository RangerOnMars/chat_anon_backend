"""
ChatAnon Backend Server - Main FastAPI Application
WebSocket-based role-playing server with token authentication
"""
import asyncio
import base64
import json
import logging
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import Dict, Optional

from server.auth import validate_token
from server.connection_manager import connection_manager, ConnectionInfo
from server.character_manager import character_manager, CharacterManager
from server.config import create_service_config, get_server_config
from services import LLMService, TTSService, ServiceConfig
from services.asr_service import ASRService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Store active sessions with their services
class SessionManager:
    """Manages LLM, TTS, and ASR services for each active session"""
    
    def __init__(self):
        self._sessions: Dict[str, Dict] = {}
    
    async def create_session(self, token: str, character_name: str) -> Dict:
        """Create a new session with services initialized"""
        # Clean up existing session if any
        if token in self._sessions:
            await self.close_session(token)
        
        # Create service config for character
        config = create_service_config(character_name)
        
        # Initialize services
        llm_service = LLMService(config)
        tts_service = TTSService(config)
        asr_service = ASRService(config)
        
        # Connect LLM and load character
        await llm_service.connect()
        llm_service.load_character(config.character_manifest_path)
        
        session = {
            "config": config,
            "llm_service": llm_service,
            "tts_service": tts_service,
            "asr_service": asr_service,
            "character_name": character_name
        }
        
        self._sessions[token] = session
        logger.info(f"Session created for token {token[:8]}... with character {character_name}")
        
        return session
    
    async def get_session(self, token: str) -> Optional[Dict]:
        """Get session by token"""
        return self._sessions.get(token)
    
    async def close_session(self, token: str):
        """Close and cleanup a session"""
        if token in self._sessions:
            session = self._sessions[token]
            
            try:
                if session.get("llm_service"):
                    await session["llm_service"].disconnect()
                if session.get("tts_service"):
                    await session["tts_service"].disconnect()
                if session.get("asr_service"):
                    await session["asr_service"].disconnect()
            except Exception as e:
                logger.warning(f"Error closing session services: {e}")
            
            del self._sessions[token]
            logger.info(f"Session closed for token {token[:8]}...")
    
    async def switch_character(self, token: str, new_character: str) -> bool:
        """Switch character for a session"""
        if token not in self._sessions:
            return False
        
        session = self._sessions[token]
        
        # Create new config for character
        config = create_service_config(new_character)
        
        # Update services
        session["llm_service"].clear_history()
        session["llm_service"].load_character(config.character_manifest_path)
        session["tts_service"].voice_id = config.character_voice_id
        session["config"] = config
        session["character_name"] = new_character
        
        logger.info(f"Character switched to {new_character} for token {token[:8]}...")
        return True


# Global session manager
session_manager = SessionManager()


async def process_text_message(websocket: WebSocket, token: str, content: str):
    """
    Process a text message: LLM -> TTS (streaming)
    
    Args:
        websocket: WebSocket connection
        token: API token for session lookup
        content: Text message content
    """
    # Get session
    session = await session_manager.get_session(token)
    if not session:
        await websocket.send_json({
            "type": "error",
            "message": "Session not found"
        })
        return
    
    llm_service = session["llm_service"]
    tts_service = session["tts_service"]
    
    # Send thinking indicator
    await websocket.send_json({
        "type": "thinking",
        "message": "Processing..."
    })
    
    # Get LLM response
    response = await llm_service.chat(content)
    
    if "error" in response:
        await websocket.send_json({
            "type": "error",
            "message": f"LLM error: {response['error']}"
        })
        return
    
    # Parse response
    raw_content = response["content"]
    cn_texts, jp_texts, emotion_labels = CharacterManager.parse_llm_response(raw_content)
    
    # Process each response with streaming TTS
    for cn_text, jp_text, emotion in zip(cn_texts, jp_texts, emotion_labels):
        # Convert names for TTS
        tts_text = CharacterManager.convert_names_for_tts(jp_text)
        
        # Stream TTS audio chunks in real-time
        try:
            async for audio_chunk in tts_service.synthesize_stream(tts_text, emotion=emotion):
                # Send each audio chunk immediately for real-time playback
                chunk_base64 = base64.b64encode(audio_chunk).decode('utf-8')
                await websocket.send_json({
                    "type": "audio_chunk",
                    "audio_base64": chunk_base64,
                    "audio_format": "pcm",
                    "audio_sample_rate": 16000
                })
            
            # Signal end of audio stream
            await websocket.send_json({
                "type": "audio_end"
            })
            
        except Exception as e:
            logger.error(f"TTS error: {e}")
        
        # Send final response with text only (audio already streamed)
        await websocket.send_json({
            "type": "response",
            "content_cn": cn_text,
            "content_jp": jp_text,
            "emotion": emotion,
            "audio_format": "pcm",
            "audio_sample_rate": 16000
        })


async def process_audio_message(websocket: WebSocket, token: str, audio_base64: str):
    """
    Process an audio message: ASR -> LLM -> TTS (streaming)
    
    Args:
        websocket: WebSocket connection
        token: API token for session lookup
        audio_base64: Base64 encoded audio data
    """
    # Get session
    session = await session_manager.get_session(token)
    if not session:
        await websocket.send_json({
            "type": "error",
            "message": "Session not found"
        })
        return
    
    asr_service = session["asr_service"]
    llm_service = session["llm_service"]
    tts_service = session["tts_service"]
    
    # Decode audio
    try:
        audio_data = base64.b64decode(audio_base64)
    except Exception as e:
        await websocket.send_json({
            "type": "error",
            "message": f"Invalid audio data: {e}"
        })
        return
    
    # Send thinking indicator
    await websocket.send_json({
        "type": "thinking",
        "message": "Transcribing..."
    })
    
    # Transcribe audio with ASR
    transcribed_texts = []
    try:
        async for text in asr_service.transcribe_audio(audio_data):
            transcribed_texts.append(text)
            # Send transcription progress
            await websocket.send_json({
                "type": "transcription",
                "text": text,
                "is_partial": True
            })
    except Exception as e:
        logger.error(f"ASR error: {e}")
        await websocket.send_json({
            "type": "error",
            "message": f"ASR error: {e}"
        })
        return
    finally:
        # Disconnect ASR after use
        await asr_service.disconnect()
    
    if not transcribed_texts:
        await websocket.send_json({
            "type": "error",
            "message": "No speech detected in audio"
        })
        return
    
    # Combine transcriptions
    full_transcription = " ".join(transcribed_texts)
    
    # Send final transcription
    await websocket.send_json({
        "type": "transcription",
        "text": full_transcription,
        "is_partial": False
    })
    
    # Process transcribed text through LLM and TTS
    await process_text_message(websocket, token, full_transcription)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    logger.info("ChatAnon Backend Server starting...")
    logger.info(f"Available characters: {[c['name'] for c in character_manager.get_available_characters()]}")
    yield
    logger.info("ChatAnon Backend Server shutting down...")
    # Cleanup all sessions
    for token in list(session_manager._sessions.keys()):
        await session_manager.close_session(token)


# Create FastAPI app
app = FastAPI(
    title="ChatAnon Backend",
    description="Role-playing chat server with character support",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Root endpoint - server info"""
    return {
        "name": "ChatAnon Backend",
        "version": "1.0.0",
        "status": "running",
        "active_connections": connection_manager.active_connections_count
    }


@app.get("/characters")
async def list_characters():
    """List available characters"""
    return {
        "characters": character_manager.get_available_characters()
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint for chat connections.
    
    Protocol:
    1. Client connects and sends: {"type": "connect", "api_token": "...", "character_name": "anon"}
    2. Server responds: {"type": "connected", "character": "anon", "message": "..."}
    3. Client sends messages: {"type": "message", "content": "..."}
    4. Server responds: {"type": "response", "content_cn": "...", "content_jp": "...", "emotion": "...", "audio_base64": "..."}
    """
    token = None
    
    try:
        # Wait for initial connection message
        # First, we need to accept to receive the first message
        await websocket.accept()
        
        # Receive connect message
        connect_data = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
        
        if connect_data.get("type") != "connect":
            await websocket.send_json({
                "type": "error",
                "message": "First message must be of type 'connect'"
            })
            await websocket.close(code=1008)
            return
        
        token = connect_data.get("api_token", "")
        character_name = connect_data.get("character_name", "anon").lower()
        
        # Validate token
        if not validate_token(token):
            await websocket.send_json({
                "type": "error",
                "message": "Invalid API token"
            })
            await websocket.close(code=1008)
            return
        
        # Validate character
        if not character_manager.is_character_available(character_name):
            await websocket.send_json({
                "type": "error",
                "message": f"Character '{character_name}' not available. Available: {[c['name'] for c in character_manager.get_available_characters()]}"
            })
            await websocket.close(code=1008)
            return
        
        # Check if token already connected (will disconnect old connection)
        if connection_manager.is_token_connected(token):
            logger.warning(f"Token {token[:8]}... reconnecting, disconnecting old session")
            await session_manager.close_session(token)
            await connection_manager.disconnect_by_token(token, "reconnect")
        
        # Register connection (re-accept is handled internally)
        # Note: we already accepted above, so we need to handle this differently
        # Store connection info manually
        conn_info = ConnectionInfo(
            websocket=websocket,
            token=token,
            character_name=character_name
        )
        connection_manager._connections[token] = conn_info
        connection_manager._websocket_to_token[websocket] = token
        
        # Create session with services
        session = await session_manager.create_session(token, character_name)
        
        # Send connection success
        char_config = character_manager.get_character_config(character_name)
        await websocket.send_json({
            "type": "connected",
            "character": character_name,
            "character_display_name": char_config.display_name if char_config else character_name,
            "message": "Connection established successfully"
        })
        
        logger.info(f"Client connected: token={token[:8]}..., character={character_name}")
        
        # Main message loop
        while True:
            try:
                data = await websocket.receive_json()
                msg_type = data.get("type", "")
                
                if msg_type == "message":
                    # Process text chat message
                    content = data.get("content", "").strip()
                    if not content:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Empty message content"
                        })
                        continue
                    
                    await process_text_message(websocket, token, content)
                    connection_manager.increment_message_count(websocket)
                
                elif msg_type == "audio_message":
                    # Process voice input (ASR -> LLM -> TTS)
                    audio_base64 = data.get("audio_base64", "")
                    if not audio_base64:
                        await websocket.send_json({
                            "type": "error",
                            "message": "No audio data provided"
                        })
                        continue
                    
                    await process_audio_message(websocket, token, audio_base64)
                    connection_manager.increment_message_count(websocket)
                
                elif msg_type == "switch_character":
                    # Switch character
                    new_character = data.get("character_name", "").lower()
                    
                    if not character_manager.is_character_available(new_character):
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Character '{new_character}' not available"
                        })
                        continue
                    
                    # Switch character in session
                    if await session_manager.switch_character(token, new_character):
                        await connection_manager.update_character(websocket, new_character)
                        char_config = character_manager.get_character_config(new_character)
                        
                        await websocket.send_json({
                            "type": "character_switched",
                            "character": new_character,
                            "character_display_name": char_config.display_name if char_config else new_character,
                            "message": f"Switched to character: {new_character}"
                        })
                    else:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Failed to switch character"
                        })
                
                elif msg_type == "clear_history":
                    # Clear conversation history
                    session = await session_manager.get_session(token)
                    if session:
                        session["llm_service"].clear_history()
                        await websocket.send_json({
                            "type": "history_cleared",
                            "message": "Conversation history cleared"
                        })
                
                elif msg_type == "ping":
                    # Keepalive ping
                    await websocket.send_json({"type": "pong"})
                
                else:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Unknown message type: {msg_type}"
                    })
                    
            except asyncio.TimeoutError:
                # Send ping to check connection
                await websocket.send_json({"type": "ping"})
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: token={token[:8] if token else 'unknown'}...")
    except asyncio.TimeoutError:
        logger.warning("Connection timeout waiting for initial message")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        # Cleanup
        if token:
            await session_manager.close_session(token)
            await connection_manager.disconnect(websocket)


def run_server():
    """Run the server using uvicorn"""
    import uvicorn
    
    config = get_server_config()
    
    # Setup logging level
    logging.getLogger().setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
    
    logger.info(f"Starting server on {config.host}:{config.port}")
    
    uvicorn.run(
        "server.main:app",
        host=config.host,
        port=config.port,
        reload=config.debug,
        log_level=config.log_level.lower()
    )


if __name__ == "__main__":
    run_server()
