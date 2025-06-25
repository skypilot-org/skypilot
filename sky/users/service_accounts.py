"""Service Account Management Module for SkyPilot.

This module provides object-oriented service account management, integrating
service account handling into the users module.
"""

import logging
import re
import secrets
from typing import Any, Dict, List, Optional

from sky import global_user_state
from sky import models
from sky.users import permission
from sky.users.token_service import token_service

logger = logging.getLogger(__name__)


class ServiceAccountManager:
    """Manager class for service account operations using object-oriented design."""

    @staticmethod
    def get_service_account_by_id(service_account_id: str) -> Optional[models.ServiceAccount]:
        """Get a service account by ID, returning ServiceAccount model."""
        user = global_user_state.get_user(service_account_id)
        if user and user.is_service_account():
            return models.ServiceAccount.from_db_record(user)
        return None

    @staticmethod
    def get_or_create_service_account(token_name: str, 
                                    creator_user_hash: str) -> models.ServiceAccount:
        """Get existing service account or create a new one."""
        # For now, always create a new service account per token
        # In the future, we could implement shared service accounts
        return models.ServiceAccount.create_service_account(token_name, creator_user_hash)

    @staticmethod
    def create_service_account_token(service_account: models.ServiceAccount,
                                   token_name: str,
                                   creator_user_hash: str,
                                   expires_in_days: Optional[int] = None) -> Dict[str, Any]:
        """Create a new service account token using the ServiceAccount model."""
        # Ensure service account exists in database
        global_user_state.add_or_update_user(service_account)
        
        # Add to permission system with default role
        permission.permission_service.add_user_if_not_exists(service_account.id)
        
        # Create JWT token
        token_data = token_service.create_token(
            creator_user_id=creator_user_hash,
            service_account_user_id=service_account.id,
            token_name=token_name,
            expires_in_days=expires_in_days
        )
        
        # Store token in database
        global_user_state.add_service_account_token(
            token_id=token_data['token_id'],
            token_name=token_name,
            token_hash=token_data['token_hash'],
            creator_user_hash=creator_user_hash,
            service_account_user_id=service_account.id,
            expires_at=token_data['expires_at']
        )
        
        return token_data

    @staticmethod
    def get_service_account_token_by_id(token_id: str) -> Optional[models.ServiceAccountToken]:
        """Get a service account token by ID, returning ServiceAccountToken model."""
        token_dict = global_user_state.get_service_account_token(token_id)
        if not token_dict:
            return None
            
        # Get the associated service account
        service_account = ServiceAccountManager.get_service_account_by_id(
            token_dict['service_account_user_id']
        )
        
        # Create a mock db record object for from_db_record method
        class MockDBRecord:
            def __init__(self, token_dict: Dict[str, Any]):
                self.token_id = token_dict['token_id']
                self.creator_user_hash = token_dict['creator_user_hash']
                self.token_name = token_dict['token_name']
                self.token_hash = token_dict['token_hash']
                self.created_at = token_dict['created_at']
                self.last_used_at = token_dict['last_used_at']
                self.expires_at = token_dict['expires_at']
        
        return models.ServiceAccountToken.from_db_record(
            MockDBRecord(token_dict), service_account
        )

    @staticmethod
    def get_all_service_account_tokens() -> List[models.ServiceAccountToken]:
        """Get all service account tokens, returning ServiceAccountToken models."""
        tokens_dict = global_user_state.get_all_service_account_tokens()
        tokens = []
        
        for token_dict in tokens_dict:
            service_account = ServiceAccountManager.get_service_account_by_id(
                token_dict['service_account_user_id']
            )
            
            # Create mock db record
            class MockDBRecord:
                def __init__(self, token_dict: Dict[str, Any]):
                    self.token_id = token_dict['token_id']
                    self.creator_user_hash = token_dict['creator_user_hash']
                    self.token_name = token_dict['token_name']
                    self.token_hash = token_dict['token_hash']
                    self.created_at = token_dict['created_at']
                    self.last_used_at = token_dict['last_used_at']
                    self.expires_at = token_dict['expires_at']
            
            token = models.ServiceAccountToken.from_db_record(
                MockDBRecord(token_dict), service_account
            )
            tokens.append(token)
        
        return tokens

    @staticmethod
    def delete_service_account_token(token_id: str) -> bool:
        """Delete a service account token."""
        return global_user_state.delete_service_account_token(token_id)

    @staticmethod
    def rotate_service_account_token(token_id: str,
                                   expires_in_days: Optional[int] = None) -> Dict[str, Any]:
        """Rotate a service account token."""
        # Get existing token info
        token = ServiceAccountManager.get_service_account_token_by_id(token_id)
        if not token:
            raise ValueError(f'Token {token_id} not found')
        
        service_account_user_id = token.get_service_account_user_id()
        if service_account_user_id is None:
            raise ValueError(f'Service account user ID not found for token {token_id}')
        
        # Generate new token
        token_data = token_service.create_token(
            creator_user_id=token.user_hash,
            service_account_user_id=service_account_user_id,
            token_name=token.token_name,
            expires_in_days=expires_in_days
        )
        
        # Update in database
        global_user_state.rotate_service_account_token(
            token_id=token_id,
            new_token_hash=token_data['token_hash'],
            new_expires_at=token_data['expires_at']
        )
        
        return token_data

    @staticmethod
    def update_token_last_used(token_id: str) -> None:
        """Update the last used timestamp for a token."""
        global_user_state.update_service_account_token_last_used(token_id)

    @staticmethod
    def get_user_service_account_tokens(user_hash: str) -> List[models.ServiceAccountToken]:
        """Get all service account tokens created by a user."""
        tokens_dict = global_user_state.get_user_service_account_tokens(user_hash)
        tokens = []
        
        for token_dict in tokens_dict:
            service_account = ServiceAccountManager.get_service_account_by_id(
                token_dict['service_account_user_id']
            )
            
            # Create mock db record
            class MockDBRecord:
                def __init__(self, token_dict: Dict[str, Any]):
                    self.token_id = token_dict['token_id']
                    self.creator_user_hash = token_dict['creator_user_hash']
                    self.token_name = token_dict['token_name']
                    self.token_hash = token_dict['token_hash']
                    self.created_at = token_dict['created_at']
                    self.last_used_at = token_dict['last_used_at']
                    self.expires_at = token_dict['expires_at']
            
            token = models.ServiceAccountToken.from_db_record(
                MockDBRecord(token_dict), service_account
            )
            tokens.append(token)
        
        return tokens


# Legacy compatibility functions that return dictionaries
# These maintain backward compatibility for existing code

def get_service_account_by_id_dict(service_account_id: str) -> Optional[Dict[str, Any]]:
    """Legacy function returning dictionary - deprecated, use ServiceAccountManager instead."""
    service_account = ServiceAccountManager.get_service_account_by_id(service_account_id)
    return service_account.to_dict() if service_account else None

def get_all_service_account_tokens_dict() -> List[Dict[str, Any]]:
    """Legacy function returning dictionaries - deprecated, use ServiceAccountManager instead."""
    tokens = ServiceAccountManager.get_all_service_account_tokens()
    return [token.to_dict() for token in tokens]

def get_user_service_account_tokens_dict(user_hash: str) -> List[Dict[str, Any]]:
    """Legacy function returning dictionaries - deprecated, use ServiceAccountManager instead."""
    tokens = ServiceAccountManager.get_user_service_account_tokens(user_hash)
    return [token.to_dict() for token in tokens]


# Create a singleton manager instance
service_account_manager = ServiceAccountManager()