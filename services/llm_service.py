"""
LLM Service - Text generation using ByteDance Doubao API
Provides character role-playing text generation
"""
import asyncio
import logging
import os
import time
from typing import List, Dict, Optional
from openai import OpenAI

from services.base import BaseService, ServiceConfig

logger = logging.getLogger(__name__)


class LLMService(BaseService):
    """LLM Service for text generation with character role-playing"""
    
    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        self.client: Optional[OpenAI] = None
        self.conversation_history: List[Dict] = []
        self.system_prompt: Optional[str] = None
        self.character_loaded = False
    
    async def connect(self):
        """Initialize OpenAI client"""
        if self._connected:
            return
        
        try:
            api_key = self.config.llm_api_key or self.config.access_key
            
            self.client = OpenAI(
                base_url=self.config.llm_endpoint,
                api_key=api_key,
            )
            self._connected = True
            logger.info(f"LLM service connected using model: {self.config.llm_model}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}")
            raise
    
    async def disconnect(self):
        """Close LLM client (no persistent connection)"""
        self.client = None
        self._connected = False
        self._closed = True
        logger.info("LLM service disconnected")
    
    def load_character(self, manifest_path: Optional[str] = None):
        """
        Load character manifest as system prompt
        
        Args:
            manifest_path: Path to character manifest file. 
                          If None, uses config.character_manifest_path
        """
        if manifest_path is None:
            manifest_path = self.config.character_manifest_path
        
        # Try relative to project root
        base_dir = os.path.dirname(os.path.dirname(__file__))
        full_path = os.path.join(base_dir, manifest_path)
        
        if not os.path.exists(full_path):
            logger.warning(f"Character manifest not found at {full_path}")
            return False
        
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                self.system_prompt = f.read()
            self.character_loaded = True
            logger.info(f"Character manifest loaded from {manifest_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load character manifest: {e}")
            raise
    
    def set_system_prompt(self, prompt: str):
        """Set custom system prompt"""
        self.system_prompt = prompt
        self.character_loaded = True
        logger.info("Custom system prompt set")
    
    def _sync_chat_completion(self, messages: List[Dict], stream: bool):
        """
        Synchronous chat completion call (runs in thread pool)
        """
        return self.client.chat.completions.create(
            model=self.config.llm_model,
            messages=messages,
            stream=stream,
            extra_body={"reasoning_effort": self.config.llm_reasoning_effort, "thinking": {"type": "enabled"}}
        )
    
    def clear_history(self):
        """Clear conversation history"""
        self.conversation_history = []
        logger.info("Conversation history cleared")
    
    def get_history(self) -> List[Dict]:
        """Get conversation history"""
        return self.conversation_history.copy()
    
    async def chat(self, user_message: str, stream: bool = None) -> Dict:
        """
        Send message to LLM and get response
        
        Args:
            user_message: User's input text
            stream: Enable streaming. If None, uses config.llm_stream
            
        Returns:
            Dict with 'content', 'elapsed_time', 'model' keys
        """
        if not self.is_connected:
            await self.connect()
        
        if stream is None:
            stream = self.config.llm_stream
        
        # Build messages
        messages = []
        
        # Add system prompt if set
        if self.system_prompt:
            messages.append({
                "role": "system",
                "content": self.system_prompt
            })
        
        # Add conversation history
        messages.extend(self.conversation_history)
        
        # Add current user message
        messages.append({
            "role": "user",
            "content": user_message
        })
        
        start_time = time.time()
        
        try:
            response = await asyncio.to_thread(
                self._sync_chat_completion,
                messages,
                stream
            )
            
            if stream:
                assistant_message = ""
                for chunk in response:
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        assistant_message += content
                elapsed_time = time.time() - start_time
            else:
                assistant_message = response.choices[0].message.content
                elapsed_time = time.time() - start_time
            
            # Save to history
            self.conversation_history.append({
                "role": "user",
                "content": user_message
            })
            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_message
            })
            
            logger.info(f"LLM response generated in {elapsed_time:.3f}s")
            return {
                "content": assistant_message,
                "elapsed_time": elapsed_time,
                "model": self.config.llm_model,
                "total_messages": len(self.conversation_history)
            }
            
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(f"LLM request failed: {e}")
            return {
                "error": str(e),
                "elapsed_time": elapsed_time
            }
    
    async def generate(self, prompt: str) -> str:
        """
        Simple generation method that returns only the response text
        """
        result = await self.chat(prompt)
        
        if "error" in result:
            raise RuntimeError(f"LLM generation failed: {result['error']}")
        
        return result["content"]
