"""
ChatAnon Backend Server - Main FastAPI Application
WebSocket-based role-playing server with token authentication
"""
import asyncio
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
from server.connection_manager import connection_manager
from server.character_manager import character_manager
from server.config import create_service_config, get_server_config, get_timeout_config
from server.message_handlers import MessageRouter, send_error
from services import LLMService, TTSService
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
        session["tts_service"].model = config.tts_model
        session["tts_service"].voice_id = config.character_voice_id
        session["config"] = config
        session["character_name"] = new_character
        
        logger.info(f"Character switched to {new_character} for token {token[:8]}...")
        return True


# Global session manager
session_manager = SessionManager()

# Global message router - initialized with session_manager for handlers that need it
message_router = MessageRouter(session_manager)


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
        # Get timeout configuration
        timeout_config = get_timeout_config()
        
        # Wait for initial connection message
        # First, we need to accept to receive the first message
        await websocket.accept()
        
        # Receive connect message with configured timeout
        connect_data = await asyncio.wait_for(
            websocket.receive_json(),
            timeout=timeout_config["websocket_connect"]
        )
        
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
        
        # Register the already-accepted connection using proper encapsulation
        connection_manager.register_accepted_connection(
            websocket=websocket,
            token=token,
            character_name=character_name
        )
        
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
        
        logger.info(f"Client connected: token={token[:20]}..., character={character_name}")
        
        # Main message loop - uses MessageRouter for clean dispatch
        connection_active = True
        while connection_active:
            try:
                data = await websocket.receive_json()
                
                # Get current session for handler
                session = await session_manager.get_session(token)
                if not session:
                    await send_error(websocket, "Session not found")
                    connection_active = False
                    continue
                
                # Route message to appropriate handler
                result = await message_router.route(websocket, token, session, data)
                
                # Handler returns True to signal exit (e.g., voice call disconnect)
                if result is True:
                    connection_active = False
                    
            except WebSocketDisconnect:
                # WebSocket disconnected during message processing
                logger.info(f"WebSocket disconnected in message loop: token={token[:8]}...")
                connection_active = False
            except asyncio.TimeoutError:
                # Send ping to check connection
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    # Failed to send ping, connection is dead
                    connection_active = False
            except RuntimeError as e:
                # Handle "WebSocket is not connected" and similar runtime errors
                if "not connected" in str(e).lower() or "disconnect" in str(e).lower():
                    logger.info(f"WebSocket connection lost: token={token[:8]}...")
                    connection_active = False
                else:
                    raise
                
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
    
    # Add file handler so logs also go to server.log
    if config.log_file:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        try:
            file_handler = logging.FileHandler(config.log_file, encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(log_format))
            logging.getLogger().addHandler(file_handler)
        except Exception as e:
            logger.warning(f"Could not add log file handler for %s: %s", config.log_file, e)
    
    use_ssl = bool(config.ssl_certfile and config.ssl_keyfile)
    if use_ssl:
        if not os.path.isfile(config.ssl_certfile):
            logger.warning(f"SSL certfile not found: {config.ssl_certfile}, starting without HTTPS")
            use_ssl = False
        elif not os.path.isfile(config.ssl_keyfile):
            logger.warning(f"SSL keyfile not found: {config.ssl_keyfile}, starting without HTTPS")
            use_ssl = False
    elif config.ssl_certfile or config.ssl_keyfile:
        logger.warning("SSL requires both ssl_certfile and ssl_keyfile; starting without HTTPS")
    
    if use_ssl:
        logger.info(f"Starting server on https://{config.host}:{config.port} (SSL enabled)")
        uvicorn.run(
            "server.main:app",
            host=config.host,
            port=config.port,
            reload=config.debug,
            log_level=config.log_level.lower(),
            ssl_certfile=config.ssl_certfile,
            ssl_keyfile=config.ssl_keyfile,
        )
    else:
        logger.info(f"Starting server on http://{config.host}:{config.port}")
        uvicorn.run(
            "server.main:app",
            host=config.host,
            port=config.port,
            reload=config.debug,
            log_level=config.log_level.lower()
        )


if __name__ == "__main__":
    run_server()
