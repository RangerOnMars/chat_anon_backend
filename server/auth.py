"""
Authentication Module - Token validation for WebSocket connections
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Import valid tokens from credentials
try:
    from credentials import VALID_API_TOKENS
except ImportError:
    logger.warning("Could not import VALID_API_TOKENS from credentials.py. Using empty list.")
    VALID_API_TOKENS = []


class TokenValidator:
    """
    Validates API tokens for client authentication.
    Tokens are loaded from secrets.py
    """
    
    def __init__(self, valid_tokens: Optional[list] = None):
        """
        Initialize the validator.
        
        Args:
            valid_tokens: List of valid tokens. If None, uses VALID_API_TOKENS from secrets.
        """
        self._valid_tokens = set(valid_tokens) if valid_tokens else set(VALID_API_TOKENS)
        logger.info(f"TokenValidator initialized with {len(self._valid_tokens)} valid tokens")
    
    def validate(self, token: str) -> bool:
        """
        Validate an API token.
        
        Args:
            token: The token to validate
            
        Returns:
            True if the token is valid, False otherwise
        """
        if not token:
            logger.warning("Empty token provided")
            return False
        
        is_valid = token in self._valid_tokens
        
        if is_valid:
            logger.debug(f"Token validated: {token[:8]}...")
        else:
            logger.warning(f"Invalid token: {token[:8]}...")
        
        return is_valid
    
    def add_token(self, token: str):
        """
        Add a new valid token at runtime.
        
        Args:
            token: The token to add
        """
        self._valid_tokens.add(token)
        logger.info(f"Token added: {token[:8]}...")
    
    def remove_token(self, token: str) -> bool:
        """
        Remove a token at runtime.
        
        Args:
            token: The token to remove
            
        Returns:
            True if the token was removed, False if it didn't exist
        """
        if token in self._valid_tokens:
            self._valid_tokens.discard(token)
            logger.info(f"Token removed: {token[:8]}...")
            return True
        return False
    
    @property
    def token_count(self) -> int:
        """Get the number of valid tokens"""
        return len(self._valid_tokens)


# Global token validator instance
token_validator = TokenValidator()


def validate_token(token: str) -> bool:
    """
    Convenience function to validate a token using the global validator.
    
    Args:
        token: The token to validate
        
    Returns:
        True if valid, False otherwise
    """
    return token_validator.validate(token)
