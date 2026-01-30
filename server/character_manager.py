"""
Character Manager - Manages character configurations and manifests
Supports loading and switching between different role-play characters
"""
import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from services.base import normalize_emotion

logger = logging.getLogger(__name__)

# Base directory for prompts
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")


@dataclass
class CharacterConfig:
    """Configuration for a character"""
    name: str
    display_name: str
    manifest_path: str
    voice_id: str
    description: str = ""
    
    @property
    def full_manifest_path(self) -> str:
        """Get the full path to the manifest file"""
        return os.path.join(BASE_DIR, self.manifest_path)


# Available characters configuration
AVAILABLE_CHARACTERS: Dict[str, CharacterConfig] = {
    "anon": CharacterConfig(
        name="anon",
        display_name="千早爱音 (Chihaya Anon)",
        manifest_path="prompts/anon/character_manifest.md",
        voice_id="AnonTokyo2026012304",
        description="MyGO!!!!! 吉他手，开朗外向的JK，高情商的乐队粘合剂"
    ),
    # Add more characters here as needed
    # "tomori": CharacterConfig(...),
}


# Japanese name pronunciation replacements for TTS
NAME_REPLACEMENTS_JP = {
    # MyGO members
    '高松燈': 'たかまつともり',
    '高松灯': 'たかまつともり',
    '燈': 'ともり',
    '灯': 'ともり',
    '千早愛音': 'ちはやあのん',
    '千早爱音': 'ちはやあのん',
    '愛音': 'あのん',
    '爱音': 'あのん',
    '椎名立希': 'しいなたき',
    '立希': 'たき',
    '長崎素世': 'ながさきそよ',
    '长崎素世': 'ながさきそよ',
    '素世': 'そよ',
    '爽世': 'そよ',
    '要楽奈': 'かなめらーな',
    '要乐奈': 'かなめらーな',
    '楽奈': 'らーな',
    '乐奈': 'らーな',
    # Ave Mujica members
    '豊川祥子': 'とがわさきこ',
    '丰川祥子': 'とがわさきこ',
    '祥子': 'さきこ',
    '三角初華': 'みすみういか',
    '三角初华': 'みすみういか',
    '初華': 'ういか',
    '初华': 'ういか',
    '若葉睦': 'わかばむつみ',
    '若叶睦': 'わかばむつみ',
    '睦': 'むつみ',
    '八幡海鈴': 'やはたうみり',
    '八幡海铃': 'やはたうみり',
    '海鈴': 'うみり',
    '海铃': 'うみり',
    '祐天寺にゃむ': 'ゆうてんじにゃむ',
    '祐天寺': 'ゆうてんじ',
    '若麦': 'にゃむ',
    '喵梦': 'にゃむ',
    # Band names
    'MyGO': 'まいご',
    'Ave Mujica': 'アヴェムジカ',
    "Poppin'Party": 'ポッピンパーティー',
    'Afterglow': 'アフターグロウ',
    'Roselia': 'ロゼリア',
    # Songs and other terms
    '春日影': 'はるひかげ',
    'live': 'ライブ',
    'Live': 'ライブ',
    'LIVE': 'ライブ',
}


class CharacterManager:
    """
    Manages character loading and switching for the role-play server.
    """
    
    def __init__(self):
        self._loaded_manifests: Dict[str, str] = {}
        self._available_characters = AVAILABLE_CHARACTERS.copy()
    
    def get_available_characters(self) -> List[Dict]:
        """
        Get list of available characters.
        
        Returns:
            List of character info dictionaries
        """
        return [
            {
                "name": config.name,
                "display_name": config.display_name,
                "description": config.description,
                "voice_id": config.voice_id
            }
            for config in self._available_characters.values()
        ]
    
    def is_character_available(self, character_name: str) -> bool:
        """Check if a character is available"""
        return character_name.lower() in self._available_characters
    
    def get_character_config(self, character_name: str) -> Optional[CharacterConfig]:
        """
        Get character configuration by name.
        
        Args:
            character_name: Name of the character
            
        Returns:
            CharacterConfig or None if not found
        """
        return self._available_characters.get(character_name.lower())
    
    def load_character_manifest(self, character_name: str) -> Optional[str]:
        """
        Load and cache character manifest.
        
        Args:
            character_name: Name of the character
            
        Returns:
            Character manifest content or None if not found
        """
        character_name = character_name.lower()
        
        # Return cached manifest if available
        if character_name in self._loaded_manifests:
            return self._loaded_manifests[character_name]
        
        config = self.get_character_config(character_name)
        if not config:
            logger.warning(f"Character '{character_name}' not found")
            return None
        
        manifest_path = config.full_manifest_path
        
        if not os.path.exists(manifest_path):
            logger.error(f"Manifest file not found: {manifest_path}")
            return None
        
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = f.read()
            
            self._loaded_manifests[character_name] = manifest
            logger.info(f"Loaded character manifest: {character_name}")
            return manifest
            
        except Exception as e:
            logger.error(f"Failed to load manifest for {character_name}: {e}")
            return None
    
    def get_voice_id(self, character_name: str) -> Optional[str]:
        """Get voice ID for a character"""
        config = self.get_character_config(character_name)
        return config.voice_id if config else None
    
    @staticmethod
    def convert_names_for_tts(text: str) -> str:
        """
        Convert character names and terms to proper Japanese readings for TTS.
        
        Args:
            text: Japanese text potentially containing character names
            
        Returns:
            Text with names replaced by their kana readings
        """
        result = text
        # Sort by length (longest first) to avoid partial replacements
        sorted_replacements = sorted(NAME_REPLACEMENTS_JP.items(), key=lambda x: len(x[0]), reverse=True)
        for original, replacement in sorted_replacements:
            result = result.replace(original, replacement)
        
        if result != text:
            logger.debug(f"TTS name conversion: {text} -> {result}")
        
        return result
    
    @staticmethod
    def parse_llm_response(raw_content: str) -> Tuple[List[str], List[str], List[str]]:
        """
        Parse LLM response JSON to extract Chinese texts, Japanese texts, and emotion labels.
        
        Expected format:
        {
            "responses_cn": ["中文回复1", "中文回复2"],
            "responses_jp": ["日本語の返事1", "日本語の返事2"],
            "responses_emotion_labels": ["happy", "auto"]
        }
        
        Args:
            raw_content: Raw LLM response content
            
        Returns:
            Tuple of (chinese_texts, japanese_texts, emotion_labels)
        """
        try:
            # Try to extract JSON from the response (handle markdown code blocks)
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', raw_content)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = raw_content.strip()

            # Be tolerant to trailing commas
            sanitized = re.sub(r",\s*([\]}])", r"\1", json_str)
            data = json.loads(sanitized)
            
            cn_texts = data.get("responses_cn", [])
            jp_texts = data.get("responses_jp", [])
            emotion_labels = data.get("responses_emotion_labels", [])
            
            # Ensure they are lists
            if isinstance(cn_texts, str):
                cn_texts = [cn_texts]
            if isinstance(jp_texts, str):
                jp_texts = [jp_texts]
            if isinstance(emotion_labels, str):
                emotion_labels = [emotion_labels]
            
            # Pad emotion_labels with "auto" if shorter than other lists
            max_len = max(len(cn_texts), len(jp_texts))
            while len(emotion_labels) < max_len:
                emotion_labels.append("auto")
            # Validate and normalize each emotion; invalid values become "auto" with warning
            emotion_labels = [normalize_emotion(e) for e in emotion_labels]

            if not cn_texts and not jp_texts:
                logger.warning("Empty responses from LLM JSON parsing")
                return [raw_content], [raw_content], ["auto"]
            
            return cn_texts, jp_texts, emotion_labels
            
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"Raw content: {raw_content}")
            return [raw_content], [raw_content], ["auto"]
        except Exception as e:
            logger.error(f"Error parsing LLM response: {e}")
            return [raw_content], [raw_content], ["auto"]


# Global character manager instance
character_manager = CharacterManager()
