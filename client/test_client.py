"""
ChatAnon Test Client - Interactive command-line client for testing the backend server
"""
import asyncio
import base64
import json
import sys
import argparse
import wave
import io
from typing import Optional

try:
    import websockets
except ImportError:
    print("Please install websockets: pip install websockets")
    sys.exit(1)


class ChatAnonClient:
    """Interactive client for ChatAnon backend server"""
    
    def __init__(self, server_url: str, api_token: str, character_name: str = "anon"):
        self.server_url = server_url
        self.api_token = api_token
        self.character_name = character_name
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self.current_character = character_name
    
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
                print(f"[Info] {response.get('message', '')}\n")
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
        if self.websocket:
            try:
                await self.websocket.close()
            except:
                pass
        self.connected = False
        print("\n[Disconnected]")
    
    async def send_message(self, content: str) -> dict:
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
                    timeout=60.0
                ))
                
                msg_type = response.get("type", "")
                
                if msg_type == "thinking":
                    print(f"  [{response.get('message', 'Thinking...')}]", end="\r")
                    continue
                
                elif msg_type == "response":
                    responses.append(response)
                    # Check if this is the last response (could add a flag in server)
                    # For now, we'll return after receiving one complete response
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


async def interactive_session(client: ChatAnonClient, save_audio: bool = False):
    """Run an interactive chat session"""
    
    print("\n" + "="*60)
    print("ChatAnon Interactive Client")
    print("="*60)
    print("Commands:")
    print("  /quit, /exit  - Exit the client")
    print("  /switch <name> - Switch character (e.g., /switch anon)")
    print("  /clear        - Clear conversation history")
    print("  /save         - Toggle audio saving")
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
                
                elif cmd == "/help":
                    print("\nCommands:")
                    print("  /quit, /exit  - Exit the client")
                    print("  /switch <name> - Switch character")
                    print("  /clear        - Clear conversation history")
                    print("  /save         - Toggle audio saving")
                    print()
                
                else:
                    print(f"[Error] Unknown command: {cmd}")
                
                continue
            
            # Send message
            result = await client.send_message(user_input)
            
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
            
        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
            break
        except EOFError:
            print("\n\nEnd of input")
            break
        except Exception as e:
            print(f"\n[Error] {e}\n")


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
        "--save-audio",
        action="store_true",
        help="Save audio responses to WAV files"
    )
    
    args = parser.parse_args()
    
    # Create client
    client = ChatAnonClient(
        server_url=args.server,
        api_token=args.token,
        character_name=args.character
    )
    
    # Connect
    if not await client.connect():
        print("Failed to connect to server")
        return 1
    
    try:
        # Run interactive session
        await interactive_session(client, save_audio=args.save_audio)
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
