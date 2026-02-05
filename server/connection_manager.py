"""
Connection Manager - Manages WebSocket connections with token-based authentication
Enforces single connection per token policy
"""
import asyncio
import logging
from typing import Dict, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime
from fastapi import WebSocket

logger = logging.getLogger(__name__)


@dataclass
class ConnectionInfo:
    """Information about an active connection"""
    websocket: WebSocket
    token: str
    character_name: str
    connected_at: datetime = field(default_factory=datetime.now)
    message_count: int = 0


class ConnectionManager:
    """
    Manages WebSocket connections with the following policies:
    - One token can only have one active connection at a time
    - New connection with same token will disconnect the old one
    """
    
    def __init__(self):
        # Map: token -> ConnectionInfo
        self._connections: Dict[str, ConnectionInfo] = {}
        # Map: websocket -> token (for reverse lookup)
        self._websocket_to_token: Dict[WebSocket, str] = {}
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
    
    async def connect(
        self, 
        websocket: WebSocket, 
        token: str, 
        character_name: str = "anon"
    ) -> bool:
        """
        Register a new connection with a token.
        If token already has an active connection, disconnects the old one.
        
        Args:
            websocket: The WebSocket connection
            token: The API token
            character_name: The character to use
            
        Returns:
            True if connection was successful
        """
        async with self._lock:
            # Check if token already has an active connection
            if token in self._connections:
                old_conn = self._connections[token]
                logger.warning(f"Token {token[:8]}... already connected. Disconnecting old connection.")
                
                try:
                    # Notify old connection before closing
                    await old_conn.websocket.send_json({
                        "type": "disconnected",
                        "reason": "new_connection",
                        "message": "Another client connected with the same token"
                    })
                    await old_conn.websocket.close(code=1008, reason="Replaced by new connection")
                except Exception as e:
                    logger.warning(f"Error closing old connection: {e}")
                
                # Clean up old connection
                if old_conn.websocket in self._websocket_to_token:
                    del self._websocket_to_token[old_conn.websocket]
            
            # Accept and register new connection
            await websocket.accept()
            
            conn_info = ConnectionInfo(
                websocket=websocket,
                token=token,
                character_name=character_name
            )
            
            self._connections[token] = conn_info
            self._websocket_to_token[websocket] = token
            
            logger.info(f"Connection established: token={token[:20]}..., character={character_name}")
            return True
    
    def register_accepted_connection(
        self,
        websocket: WebSocket,
        token: str,
        character_name: str = "anon"
    ) -> ConnectionInfo:
        """
        Register an already-accepted WebSocket connection.
        
        Use this method when the WebSocket has already been accepted
        (e.g., to receive initial authentication message).
        
        Args:
            websocket: The already-accepted WebSocket connection
            token: The API token
            character_name: The character to use
            
        Returns:
            ConnectionInfo for the registered connection
        """
        conn_info = ConnectionInfo(
            websocket=websocket,
            token=token,
            character_name=character_name
        )
        
        self._connections[token] = conn_info
        self._websocket_to_token[websocket] = token
        
        logger.info(f"Connection registered: token={token[:8]}..., character={character_name}")
        return conn_info
    
    async def disconnect(self, websocket: WebSocket) -> Optional[str]:
        """
        Remove a connection from the manager.
        
        Args:
            websocket: The WebSocket to disconnect
            
        Returns:
            The token that was disconnected, or None if not found
        """
        async with self._lock:
            token = self._websocket_to_token.get(websocket)
            
            if token:
                del self._websocket_to_token[websocket]
                if token in self._connections:
                    del self._connections[token]
                logger.info(f"Connection closed: token={token[:8]}...")
                return token
            
            return None
    
    async def disconnect_by_token(self, token: str, reason: str = "server_disconnect"):
        """
        Disconnect a connection by its token.
        
        Args:
            token: The API token
            reason: Reason for disconnection
        """
        async with self._lock:
            if token in self._connections:
                conn = self._connections[token]
                
                try:
                    await conn.websocket.send_json({
                        "type": "disconnected",
                        "reason": reason
                    })
                    await conn.websocket.close(code=1000)
                except Exception as e:
                    logger.warning(f"Error during disconnect: {e}")
                
                if conn.websocket in self._websocket_to_token:
                    del self._websocket_to_token[conn.websocket]
                del self._connections[token]
                
                logger.info(f"Disconnected by token: {token[:8]}... reason={reason}")
    
    def get_connection(self, token: str) -> Optional[ConnectionInfo]:
        """Get connection info by token"""
        return self._connections.get(token)
    
    def get_connection_by_websocket(self, websocket: WebSocket) -> Optional[ConnectionInfo]:
        """Get connection info by websocket"""
        token = self._websocket_to_token.get(websocket)
        if token:
            return self._connections.get(token)
        return None
    
    def is_token_connected(self, token: str) -> bool:
        """Check if a token has an active connection"""
        return token in self._connections
    
    async def update_character(self, websocket: WebSocket, character_name: str) -> bool:
        """
        Update the character for a connection.
        
        Args:
            websocket: The WebSocket connection
            character_name: New character name
            
        Returns:
            True if update was successful
        """
        async with self._lock:
            token = self._websocket_to_token.get(websocket)
            if token and token in self._connections:
                self._connections[token].character_name = character_name
                logger.info(f"Character updated: token={token[:8]}..., character={character_name}")
                return True
            return False
    
    def increment_message_count(self, websocket: WebSocket):
        """Increment the message count for a connection"""
        token = self._websocket_to_token.get(websocket)
        if token and token in self._connections:
            self._connections[token].message_count += 1
    
    @property
    def active_connections_count(self) -> int:
        """Get the number of active connections"""
        return len(self._connections)
    
    def get_all_tokens(self) -> Set[str]:
        """Get all connected tokens"""
        return set(self._connections.keys())
    
    async def broadcast(self, message: dict, exclude_token: Optional[str] = None):
        """
        Broadcast a message to all connected clients.
        
        Args:
            message: The message to broadcast
            exclude_token: Optional token to exclude from broadcast
        """
        for token, conn in self._connections.items():
            if token != exclude_token:
                try:
                    await conn.websocket.send_json(message)
                except Exception as e:
                    logger.warning(f"Error broadcasting to {token[:8]}...: {e}")


# Global connection manager instance
connection_manager = ConnectionManager()
