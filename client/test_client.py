"""
ChatAnon Test Client - Interactive command-line client for testing the backend server
Supports both text mode and voice mode with real-time audio playback
"""
import asyncio
import base64
import json
import sys
import argparse
import wave
import io
import logging
from typing import Optional

try:
    import websockets
except ImportError:
    print("Please install websockets: pip install websockets")
    sys.exit(1)

# Try to import audio components
try:
    from client.audio_manager import (
        AudioManager, AudioPlayer, AudioRecorder, AudioConfig,
        PYAUDIO_AVAILABLE, NUMPY_AVAILABLE
    )
except ImportError:
    try:
        from audio_manager import (
            AudioManager, AudioPlayer, AudioRecorder, AudioConfig,
            PYAUDIO_AVAILABLE, NUMPY_AVAILABLE
        )
    except ImportError:
        PYAUDIO_AVAILABLE = False
        NUMPY_AVAILABLE = False
        AudioManager = None
        AudioPlayer = None
        AudioRecorder = None
        AudioConfig = None

logger = logging.getLogger(__name__)


class ChatAnonClient:
    """Interactive client for ChatAnon backend server with audio support"""
    
    def __init__(self, server_url: str, api_token: str, character_name: str = "anon", 
                 enable_audio: bool = True):
        self.server_url = server_url
        self.api_token = api_token
        self.character_name = character_name
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self.current_character = character_name
        
        # Audio components
        self.enable_audio = enable_audio and PYAUDIO_AVAILABLE
        self.audio_manager: Optional[AudioManager] = None
        self.audio_player: Optional[AudioPlayer] = None
        self.audio_recorder: Optional[AudioRecorder] = None
        
        if self.enable_audio:
            try:
                self.audio_manager = AudioManager()
                self.audio_manager.initialize()
                self.audio_player = AudioPlayer(
                    self.audio_manager, 
                    AudioConfig(sample_rate=16000, channels=1)
                )
                self.audio_recorder = AudioRecorder(
                    self.audio_manager,
                    AudioConfig(sample_rate=16000, channels=1)
                )
                logger.info("Audio components initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize audio: {e}")
                self.enable_audio = False
    
    async def connect(self) -> bool:
        """Connect to the server"""
        try:
            print(f"Connecting to {self.server_url}...")
            self.websocket = await websockets.connect(self.server_url)
            
            # Send connect message
            connect_msg = {
                "type": "connect",
                "api_token": self.api_token,
                "character_name": self.character_name
            }
            await self.websocket.send(json.dumps(connect_msg))
            
            # Wait for response
            response = json.loads(await self.websocket.recv())
            
            if response.get("type") == "connected":
                self.connected = True
                self.current_character = response.get("character", self.character_name)
                print(f"\n[Connected] Character: {response.get('character_display_name', self.current_character)}")
                print(f"[Info] {response.get('message', '')}")
                if self.enable_audio:
                    print("[Audio] Real-time audio playback enabled")
                else:
                    print("[Audio] Audio playback disabled (pyaudio not available)")
                print()
                return True
            elif response.get("type") == "error":
                print(f"\n[Error] {response.get('message', 'Connection failed')}")
                return False
            else:
                print(f"\n[Error] Unexpected response: {response}")
                return False
                
        except Exception as e:
            print(f"\n[Error] Connection failed: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from the server"""
        # Stop audio components
        if self.audio_player:
            self.audio_player.stop()
        if self.audio_recorder and self.audio_recorder.is_recording:
            self.audio_recorder.stop_recording()
        if self.audio_manager:
            self.audio_manager.close_all()
        
        if self.websocket:
            try:
                await self.websocket.close()
            except:
                pass
        self.connected = False
        print("\n[Disconnected]")
    
    def play_audio(self, audio_base64: str):
        """Play audio from base64 encoded data"""
        if not self.enable_audio or not self.audio_player:
            return False
        
        try:
            audio_bytes = base64.b64decode(audio_base64)
            self.audio_player.play(audio_bytes)
            return True
        except Exception as e:
            logger.error(f"Error playing audio: {e}")
            return False
    
    def play_audio_bytes(self, audio_bytes: bytes):
        """Play raw audio bytes"""
        if not self.enable_audio or not self.audio_player:
            return False
        
        try:
            self.audio_player.play(audio_bytes)
            return True
        except Exception as e:
            logger.error(f"Error playing audio: {e}")
            return False
    
    async def send_message(self, content: str, play_audio: bool = True) -> dict:
        """Send a chat message and receive response"""
        if not self.connected or not self.websocket:
            return {"error": "Not connected"}
        
        try:
            # Send message
            await self.websocket.send(json.dumps({
                "type": "message",
                "content": content
            }))
            
            responses = []
            
            # Receive responses
            while True:
                response = json.loads(await asyncio.wait_for(
                    self.websocket.recv(), 
                    timeout=120.0
                ))
                
                msg_type = response.get("type", "")
                
                if msg_type == "thinking":
                    print(f"  [{response.get('message', 'Thinking...')}]", end="\r")
                    continue
                
                elif msg_type == "audio_chunk":
                    # Streaming audio chunk - play immediately
                    if play_audio and response.get("audio_base64"):
                        self.play_audio(response["audio_base64"])
                    continue
                
                elif msg_type == "audio_end":
                    # Audio streaming complete
                    continue
                
                elif msg_type == "response":
                    responses.append(response)
                    # DON'T play audio here - already played via streaming audio_chunk
                    # Audio in response is only for saving to file if needed
                    return {"responses": responses}
                
                elif msg_type == "error":
                    return {"error": response.get("message", "Unknown error")}
                
                elif msg_type == "ping":
                    await self.websocket.send(json.dumps({"type": "pong"}))
                    continue
                
                else:
                    # Unknown type, might be end of responses
                    if responses:
                        return {"responses": responses}
                    continue
                    
        except asyncio.TimeoutError:
            if responses:
                return {"responses": responses}
            return {"error": "Response timeout"}
        except Exception as e:
            return {"error": str(e)}
    
    async def switch_character(self, character_name: str) -> bool:
        """Switch to a different character"""
        if not self.connected or not self.websocket:
            print("[Error] Not connected")
            return False
        
        try:
            await self.websocket.send(json.dumps({
                "type": "switch_character",
                "character_name": character_name
            }))
            
            response = json.loads(await asyncio.wait_for(
                self.websocket.recv(),
                timeout=30.0
            ))
            
            if response.get("type") == "character_switched":
                self.current_character = character_name
                print(f"\n[Switched] Now chatting with: {response.get('character_display_name', character_name)}")
                return True
            elif response.get("type") == "error":
                print(f"\n[Error] {response.get('message', 'Switch failed')}")
                return False
            else:
                print(f"\n[Error] Unexpected response: {response}")
                return False
                
        except Exception as e:
            print(f"\n[Error] Switch failed: {e}")
            return False
    
    async def clear_history(self) -> bool:
        """Clear conversation history"""
        if not self.connected or not self.websocket:
            print("[Error] Not connected")
            return False
        
        try:
            await self.websocket.send(json.dumps({
                "type": "clear_history"
            }))
            
            response = json.loads(await asyncio.wait_for(
                self.websocket.recv(),
                timeout=10.0
            ))
            
            if response.get("type") == "history_cleared":
                print("\n[Info] Conversation history cleared")
                return True
            else:
                print(f"\n[Error] {response.get('message', 'Clear failed')}")
                return False
                
        except Exception as e:
            print(f"\n[Error] Clear failed: {e}")
            return False
    
    def save_audio(self, audio_base64: str, filename: str = "response.wav"):
        """Save audio response to a WAV file"""
        try:
            audio_bytes = base64.b64decode(audio_base64)
            
            # Write as WAV file (PCM 16-bit, 16kHz, mono)
            with wave.open(filename, 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(16000)
                wav_file.writeframes(audio_bytes)
            
            print(f"  [Audio saved to {filename}]")
            return True
        except Exception as e:
            print(f"  [Error saving audio: {e}]")
            return False
    
    def stop_audio(self):
        """Stop any playing audio"""
        if self.audio_player:
            self.audio_player.clear_queue()
            print("[Audio] Playback stopped")


async def interactive_session(client: ChatAnonClient, save_audio: bool = False, 
                              play_audio: bool = True):
    """Run an interactive chat session"""
    
    print("\n" + "="*60)
    print("ChatAnon Interactive Client")
    print("="*60)
    print("Commands:")
    print("  /quit, /exit  - Exit the client")
    print("  /switch <name> - Switch character (e.g., /switch anon)")
    print("  /clear        - Clear conversation history")
    print("  /save         - Toggle audio saving to file")
    print("  /play         - Toggle real-time audio playback")
    print("  /stop         - Stop current audio playback")
    print("  /help         - Show this help")
    print("="*60 + "\n")
    
    audio_count = 0
    
    while True:
        try:
            # Get user input
            user_input = input(f"You: ").strip()
            
            if not user_input:
                continue
            
            # Handle commands
            if user_input.startswith("/"):
                parts = user_input.split(maxsplit=1)
                cmd = parts[0].lower()
                args = parts[1] if len(parts) > 1 else ""
                
                if cmd in ["/quit", "/exit"]:
                    print("\nGoodbye!")
                    break
                
                elif cmd == "/switch":
                    if args:
                        await client.switch_character(args)
                    else:
                        print("[Error] Usage: /switch <character_name>")
                
                elif cmd == "/clear":
                    await client.clear_history()
                
                elif cmd == "/save":
                    save_audio = not save_audio
                    print(f"[Info] Audio saving: {'ON' if save_audio else 'OFF'}")
                
                elif cmd == "/play":
                    play_audio = not play_audio
                    print(f"[Info] Audio playback: {'ON' if play_audio else 'OFF'}")
                
                elif cmd == "/stop":
                    client.stop_audio()
                
                elif cmd == "/help":
                    print("\nCommands:")
                    print("  /quit, /exit  - Exit the client")
                    print("  /switch <name> - Switch character")
                    print("  /clear        - Clear conversation history")
                    print("  /save         - Toggle audio saving")
                    print("  /play         - Toggle audio playback")
                    print("  /stop         - Stop audio playback")
                    print()
                
                else:
                    print(f"[Error] Unknown command: {cmd}")
                
                continue
            
            # Send message
            result = await client.send_message(user_input, play_audio=play_audio)
            
            if "error" in result:
                print(f"\n[Error] {result['error']}\n")
                continue
            
            # Display responses
            for resp in result.get("responses", []):
                cn_text = resp.get("content_cn", "")
                jp_text = resp.get("content_jp", "")
                emotion = resp.get("emotion", "auto")
                
                print(f"\n{client.current_character.title()}: {cn_text}")
                print(f"  (JP: {jp_text})")
                print(f"  [emotion: {emotion}]")
                
                # Save audio if enabled and available
                if save_audio and resp.get("audio_base64"):
                    audio_count += 1
                    client.save_audio(resp["audio_base64"], f"response_{audio_count:03d}.wav")
            
            print()
            
            # Wait for audio to finish playing
            if play_audio and client.audio_player:
                client.audio_player.wait_until_done()
            
        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
            break
        except EOFError:
            print("\n\nEnd of input")
            break
        except Exception as e:
            print(f"\n[Error] {e}\n")


async def voice_mode_session(client: ChatAnonClient):
    """
    Run a voice conversation session.
    Records audio from microphone, sends to server, plays response.
    """
    if not client.enable_audio:
        print("[Error] Audio not available. Install pyaudio: pip install pyaudio")
        return
    
    print("\n" + "="*60)
    print("ChatAnon Voice Mode")
    print("="*60)
    print("Press Enter to start recording, Enter again to stop and send.")
    print("Type 'quit' to exit voice mode.")
    print("="*60 + "\n")
    
    while True:
        try:
            cmd = input("[Press Enter to speak, or 'quit' to exit]: ").strip().lower()
            
            if cmd == 'quit':
                print("Exiting voice mode...")
                break
            
            # Start recording
            print("Recording... (press Enter to stop)")
            client.audio_recorder.start_recording()
            
            # Record until Enter is pressed
            recording_task = asyncio.create_task(
                asyncio.get_event_loop().run_in_executor(None, input, "")
            )
            
            # Read audio chunks while recording
            while not recording_task.done():
                chunk = client.audio_recorder.read_chunk()
                await asyncio.sleep(0.02)  # 20ms chunks
            
            # Stop recording
            audio_data = client.audio_recorder.stop_recording()
            print(f"Recorded {len(audio_data)} bytes of audio")
            
            if len(audio_data) < 3200:  # Less than 0.1 second
                print("[Warning] Recording too short, skipping...")
                continue
            
            # Send audio to server for ASR + LLM + TTS
            print("Sending to server...")
            
            await client.websocket.send(json.dumps({
                "type": "audio_message",
                "audio_base64": base64.b64encode(audio_data).decode('utf-8'),
                "audio_format": "pcm",
                "sample_rate": 16000
            }))
            
            # Receive response
            while True:
                response = json.loads(await asyncio.wait_for(
                    client.websocket.recv(),
                    timeout=60.0
                ))
                
                msg_type = response.get("type", "")
                
                if msg_type == "transcription":
                    print(f"You said: {response.get('text', '')}")
                
                elif msg_type == "thinking":
                    print(f"  [{response.get('message', 'Thinking...')}]", end="\r")
                
                elif msg_type == "audio_chunk":
                    # Play streaming audio
                    if response.get("audio_base64"):
                        client.play_audio(response["audio_base64"])
                
                elif msg_type == "response":
                    cn_text = response.get("content_cn", "")
                    jp_text = response.get("content_jp", "")
                    print(f"\n{client.current_character.title()}: {cn_text}")
                    print(f"  (JP: {jp_text})")
                    # DON'T play audio here - already played via streaming audio_chunk
                    break
                
                elif msg_type == "error":
                    print(f"[Error] {response.get('message', 'Unknown error')}")
                    break
            
            # Wait for audio to finish
            if client.audio_player:
                client.audio_player.wait_until_done()
            print()
            
        except asyncio.TimeoutError:
            print("[Error] Response timeout")
        except KeyboardInterrupt:
            print("\n\nExiting voice mode...")
            break
        except Exception as e:
            print(f"\n[Error] {e}")


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="ChatAnon Test Client")
    parser.add_argument(
        "--server", "-s",
        default="ws://localhost:8765/ws",
        help="Server WebSocket URL (default: ws://localhost:8765/ws)"
    )
    parser.add_argument(
        "--token", "-t",
        default="dev_token_001",
        help="API token for authentication"
    )
    parser.add_argument(
        "--character", "-c",
        default="anon",
        help="Character name (default: anon)"
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["text", "voice"],
        default="text",
        help="Interaction mode: text or voice (default: text)"
    )
    parser.add_argument(
        "--save-audio",
        action="store_true",
        help="Save audio responses to WAV files"
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Disable audio playback"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    log_level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(level=log_level, format='%(name)s - %(levelname)s - %(message)s')
    
    # Check audio availability
    if args.mode == "voice" and not PYAUDIO_AVAILABLE:
        print("[Error] Voice mode requires pyaudio. Install with: pip install pyaudio")
        return 1
    
    # Create client
    client = ChatAnonClient(
        server_url=args.server,
        api_token=args.token,
        character_name=args.character,
        enable_audio=not args.no_audio
    )
    
    # Connect
    if not await client.connect():
        print("Failed to connect to server")
        return 1
    
    try:
        if args.mode == "voice":
            await voice_mode_session(client)
        else:
            await interactive_session(
                client, 
                save_audio=args.save_audio,
                play_audio=not args.no_audio
            )
    finally:
        await client.disconnect()
    
    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
