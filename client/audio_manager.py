"""
Audio Manager - Handles microphone input and speaker output using PyAudio
Based on the reference ChatAnon agent implementation
"""
import logging
import queue
import threading
from dataclasses import dataclass
from typing import Optional

try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    pyaudio = None

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None

logger = logging.getLogger(__name__)


@dataclass
class AudioConfig:
    """Audio stream configuration"""
    sample_rate: int = 16000
    channels: int = 1
    format: int = 8  # pyaudio.paInt16 = 8
    bits_per_sample: int = 16
    chunk_size: Optional[int] = None
    # Buffer limits
    max_buffer_seconds: float = 300.0  # Max 5 minutes of audio in buffer
    max_queue_size: int = 1000  # Max items in playback queue
    
    def __post_init__(self):
        if self.chunk_size is None:
            # Default: 20ms frames
            self.chunk_size = self.sample_rate // 50
        if PYAUDIO_AVAILABLE:
            self.format = pyaudio.paInt16
    
    @property
    def max_buffer_bytes(self) -> int:
        """Calculate max buffer size in bytes based on max_buffer_seconds"""
        bytes_per_sample = self.bits_per_sample // 8
        bytes_per_second = self.sample_rate * self.channels * bytes_per_sample
        return int(bytes_per_second * self.max_buffer_seconds)


class AudioManager:
    """Unified audio I/O management using PyAudio"""
    
    def __init__(self):
        if not PYAUDIO_AVAILABLE:
            raise ImportError("pyaudio is required for audio features. Install with: pip install pyaudio")
        
        self.pyaudio_instance: Optional[pyaudio.PyAudio] = None
        self.input_stream: Optional[pyaudio.Stream] = None
        self.output_stream: Optional[pyaudio.Stream] = None
        self._initialized = False
    
    def initialize(self):
        """Initialize PyAudio instance"""
        if not self._initialized:
            self.pyaudio_instance = pyaudio.PyAudio()
            self._initialized = True
            logger.info("AudioManager initialized")
    
    def open_input_stream(self, config: AudioConfig) -> pyaudio.Stream:
        """Open microphone input stream"""
        if not self._initialized:
            self.initialize()
        
        try:
            self.input_stream = self.pyaudio_instance.open(
                format=config.format,
                channels=config.channels,
                rate=config.sample_rate,
                input=True,
                frames_per_buffer=config.chunk_size
            )
            logger.info(f"Input stream opened: {config.sample_rate}Hz, {config.channels}ch")
            return self.input_stream
        except Exception as e:
            logger.error(f"Failed to open input stream: {e}")
            raise
    
    def open_output_stream(self, config: AudioConfig) -> pyaudio.Stream:
        """Open speaker output stream"""
        if not self._initialized:
            self.initialize()
        
        try:
            self.output_stream = self.pyaudio_instance.open(
                format=config.format,
                channels=config.channels,
                rate=config.sample_rate,
                output=True,
                frames_per_buffer=config.chunk_size
            )
            logger.info(f"Output stream opened: {config.sample_rate}Hz, {config.channels}ch")
            return self.output_stream
        except Exception as e:
            logger.error(f"Failed to open output stream: {e}")
            raise
    
    def read_chunk(self, num_frames: int, exception_on_overflow: bool = False) -> bytes:
        """Read audio chunk from input stream"""
        if not self.input_stream:
            raise RuntimeError("Input stream not opened")
        return self.input_stream.read(num_frames, exception_on_overflow=exception_on_overflow)
    
    def write_chunk(self, data: bytes):
        """Write audio chunk to output stream"""
        if not self.output_stream:
            raise RuntimeError("Output stream not opened")
        self.output_stream.write(data)
    
    def close_input_stream(self):
        """Close microphone input stream"""
        if self.input_stream:
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception as e:
                logger.warning(f"Error closing input stream: {e}")
            self.input_stream = None
            logger.info("Input stream closed")
    
    def close_output_stream(self):
        """Close speaker output stream"""
        if self.output_stream:
            try:
                self.output_stream.stop_stream()
                self.output_stream.close()
            except Exception as e:
                logger.warning(f"Error closing output stream: {e}")
            self.output_stream = None
            logger.info("Output stream closed")
    
    def close_all(self):
        """Close all streams and terminate PyAudio"""
        self.close_input_stream()
        self.close_output_stream()
        
        if self.pyaudio_instance:
            self.pyaudio_instance.terminate()
            self.pyaudio_instance = None
            self._initialized = False
            logger.info("AudioManager terminated")
    
    def list_devices(self):
        """List available audio devices"""
        if not self._initialized:
            self.initialize()
        
        device_count = self.pyaudio_instance.get_device_count()
        logger.info(f"Available audio devices: {device_count}")
        
        devices = []
        for i in range(device_count):
            info = self.pyaudio_instance.get_device_info_by_index(i)
            devices.append({
                "index": i,
                "name": info['name'],
                "max_input_channels": info['maxInputChannels'],
                "max_output_channels": info['maxOutputChannels'],
            })
            logger.info(f"  [{i}] {info['name']} - "
                       f"In: {info['maxInputChannels']}, "
                       f"Out: {info['maxOutputChannels']}")
        return devices


class AudioPlayer:
    """
    Queue-based audio player for real-time playback.
    Uses a separate thread to play audio chunks without blocking.
    """
    
    def __init__(self, audio_manager: AudioManager, config: Optional[AudioConfig] = None):
        self.audio_manager = audio_manager
        self.config = config or AudioConfig()
        
        # Use bounded queue to prevent memory issues
        self.audio_queue: queue.Queue = queue.Queue(maxsize=self.config.max_queue_size)
        self.is_playing = False
        self.play_thread: Optional[threading.Thread] = None
        self._stop_signal = False
        self._done_event = threading.Event()  # Reusable event for wait_until_done
    
    def start(self):
        """Start the audio player"""
        if self.is_playing:
            return
        
        # Open output stream
        self.audio_manager.open_output_stream(self.config)
        
        self.is_playing = True
        self._stop_signal = False
        
        # Start playback thread
        self.play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self.play_thread.start()
        logger.info("AudioPlayer started")
    
    def _play_worker(self):
        """Worker thread for audio playback"""
        while not self._stop_signal or not self.audio_queue.empty():
            try:
                audio_chunk = self.audio_queue.get(timeout=0.1)
                if audio_chunk is None:  # End signal
                    break
                self.audio_manager.write_chunk(audio_chunk)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error playing audio: {e}")
    
    def play(self, audio_bytes: bytes):
        """
        Queue audio data for playback.
        
        If the queue is full, drops the oldest audio to make room.
        """
        if not self.is_playing:
            self.start()
        
        try:
            self.audio_queue.put_nowait(audio_bytes)
        except queue.Full:
            # Queue is full, drop oldest to make room
            logger.warning("Audio queue full, dropping oldest chunk")
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.put_nowait(audio_bytes)
            except queue.Empty:
                pass
    
    def stop(self, wait: bool = True):
        """Stop the audio player"""
        if not self.is_playing:
            return
        
        self._stop_signal = True
        try:
            self.audio_queue.put_nowait(None)  # Send end signal
        except queue.Full:
            # Clear queue and add end signal
            self.clear_queue()
            self.audio_queue.put_nowait(None)
        
        if wait and self.play_thread:
            self.play_thread.join(timeout=2.0)
        
        self.play_thread = None
        self.is_playing = False
        self.audio_manager.close_output_stream()
        logger.info("AudioPlayer stopped")
    
    def clear_queue(self):
        """Clear any pending audio in the queue"""
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
    
    def wait_until_done(self, timeout: float = None):
        """
        Wait until all queued audio has been played.
        
        Args:
            timeout: Maximum time to wait in seconds. None for no limit.
        """
        import time
        start_time = time.time()
        
        while not self.audio_queue.empty():
            if timeout and (time.time() - start_time) > timeout:
                logger.warning("wait_until_done timed out")
                break
            self._done_event.wait(0.1)
            self._done_event.clear()


class AudioRecorder:
    """
    Audio recorder for capturing microphone input.
    Can stream audio chunks or record to buffer.
    
    Buffer size is limited by config.max_buffer_bytes to prevent memory issues
    during long recordings.
    """
    
    def __init__(self, audio_manager: AudioManager, config: Optional[AudioConfig] = None):
        self.audio_manager = audio_manager
        self.config = config or AudioConfig()
        
        self.is_recording = False
        self._stop_signal = False
        self.audio_buffer: bytearray = bytearray()
        self._buffer_overflow_warned = False
    
    def start_recording(self):
        """Start recording from microphone"""
        if self.is_recording:
            return
        
        # Open input stream
        self.audio_manager.open_input_stream(self.config)
        self.is_recording = True
        self._stop_signal = False
        self.audio_buffer = bytearray()
        self._buffer_overflow_warned = False
        logger.info(f"AudioRecorder started (max buffer: {self.config.max_buffer_bytes / 1024 / 1024:.1f}MB)")
    
    def stop_recording(self) -> bytes:
        """Stop recording and return captured audio"""
        if not self.is_recording:
            return bytes(self.audio_buffer)
        
        self._stop_signal = True
        self.is_recording = False
        self.audio_manager.close_input_stream()
        logger.info(f"AudioRecorder stopped (buffer size: {len(self.audio_buffer) / 1024:.1f}KB)")
        return bytes(self.audio_buffer)
    
    def read_chunk(self) -> Optional[bytes]:
        """
        Read a single chunk of audio data.
        
        If buffer is at max capacity, oldest data is discarded to make room.
        """
        if not self.is_recording:
            return None
        
        try:
            chunk = self.audio_manager.read_chunk(
                self.config.chunk_size,
                exception_on_overflow=False
            )
            
            # Check buffer size limit
            new_size = len(self.audio_buffer) + len(chunk)
            if new_size > self.config.max_buffer_bytes:
                # Discard oldest data to make room
                bytes_to_remove = new_size - self.config.max_buffer_bytes
                self.audio_buffer = self.audio_buffer[bytes_to_remove:]
                
                if not self._buffer_overflow_warned:
                    logger.warning(
                        f"Audio buffer limit reached ({self.config.max_buffer_seconds}s / "
                        f"{self.config.max_buffer_bytes / 1024 / 1024:.1f}MB), "
                        "discarding oldest audio"
                    )
                    self._buffer_overflow_warned = True
            
            self.audio_buffer.extend(chunk)
            return chunk
        except Exception as e:
            logger.error(f"Error reading audio: {e}")
            return None
    
    def get_buffer(self) -> bytes:
        """Get the current audio buffer"""
        return bytes(self.audio_buffer)
    
    def clear_buffer(self):
        """Clear the audio buffer"""
        self.audio_buffer = bytearray()
        self._buffer_overflow_warned = False
    
    @property
    def buffer_duration_seconds(self) -> float:
        """Get the current buffer duration in seconds"""
        bytes_per_sample = self.config.bits_per_sample // 8
        bytes_per_second = self.config.sample_rate * self.config.channels * bytes_per_sample
        return len(self.audio_buffer) / bytes_per_second if bytes_per_second > 0 else 0.0
