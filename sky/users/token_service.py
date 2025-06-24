"""JWT-based service account token management for SkyPilot."""

import datetime
import hashlib
import os
import secrets
from typing import Any, Dict, Optional

import jwt

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# JWT Configuration
JWT_ALGORITHM = 'HS256'
JWT_ISSUER = 'sky'  # Shortened for compact tokens
JWT_SECRET_ENV = 'SKYPILOT_JWT_SECRET'


class TokenService:
    """Service for managing JWT-based service account tokens."""

    def __init__(self):
        self.secret_key = self._get_or_generate_secret()

    def _get_or_generate_secret(self) -> str:
        """Get JWT secret from environment or generate a new one."""
        secret = os.environ.get(JWT_SECRET_ENV)
        if not secret:
            # Generate a secure random secret
            secret = secrets.token_urlsafe(64)
            logger.warning(
                f'No JWT secret found in {JWT_SECRET_ENV}, generated a new '
                'one. '
                'For production, set a persistent secret in the environment '
                'variable.')
        return secret

    def create_token(self,
                     creator_user_id: str,
                     service_account_user_id: str,
                     token_name: str,
                     expires_in_days: Optional[int] = None) -> Dict[str, Any]:
        """Create a new JWT service account token.

        Args:
            creator_user_id: The creator's user hash
            service_account_user_id: The service account's own user ID
            token_name: Descriptive name for the token
            expires_in_days: Optional expiration in days

        Returns:
            Dict containing token info including the JWT token
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        token_id = secrets.token_urlsafe(12)  # Shorter ID for JWT

        # Build minimal JWT payload with single-character field names for
        # compactness
        payload = {
            'i': JWT_ISSUER,  # Issuer (use constant)
            't': int(now.timestamp()),  # Issued at (shortened from 'iat')
            # Service account user ID (shortened from 'sub')
            'u': service_account_user_id,
            'k': token_id,  # Token ID (shortened from 'token_id')
            'y': 'sa',  # Type: service account (shortened from 'type')
        }

        # Add expiration if specified
        expires_at = None
        if expires_in_days:
            exp_time = now + datetime.timedelta(days=expires_in_days)
            payload['e'] = int(
                exp_time.timestamp())  # Expiration (shortened from 'exp')
            expires_at = int(exp_time.timestamp())

        # Generate JWT
        jwt_token = jwt.encode(payload,
                               self.secret_key,
                               algorithm=JWT_ALGORITHM)

        # Create token with SkyPilot prefix
        full_token = f'sky_{jwt_token}'

        # Generate hash for database storage (we still hash the full token)
        token_hash = hashlib.sha256(full_token.encode()).hexdigest()

        return {
            'token_id': token_id,
            'token': full_token,
            'token_hash': token_hash,
            'creator_user_id': creator_user_id,
            'service_account_user_id': service_account_user_id,
            'token_name': token_name,
            'created_at': int(now.timestamp()),
            'expires_at': expires_at,
        }

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify and decode a JWT token.

        Args:
            token: The full token (with sky_ prefix)

        Returns:
            Decoded token payload or None if invalid
        """
        if not token.startswith('sky_'):
            return None

        # Remove the sky_ prefix
        jwt_token = token[4:]

        try:
            # Decode and verify JWT
            payload = jwt.decode(
                jwt_token,
                self.secret_key,
                algorithms=[JWT_ALGORITHM],
                issuer=JWT_ISSUER)  # Use constant for consistency

            # Verify token type
            token_type = payload.get('y')
            if token_type != 'sa':
                logger.warning(f'Invalid token type: {token_type}')
                return None

            # Convert shortened field names back to standard names for
            # compatibility
            normalized_payload = {
                'iss': payload.get('i'),  # issuer
                'iat': payload.get('t'),  # issued at
                'sub': payload.get('u'),  # subject (service account user ID)
                'token_id': payload.get('k'),  # token ID
                'type': 'service_account',  # expand shortened type
            }

            # Add expiration if present
            if 'e' in payload:
                normalized_payload['exp'] = payload['e']

            return normalized_payload

        except jwt.ExpiredSignatureError:
            logger.debug('Token has expired')
            return None
        except jwt.InvalidTokenError as e:
            logger.debug(f'Invalid token: {e}')
            return None

    def get_token_hash(self, token: str) -> str:
        """Get hash of a token for database lookup."""
        return hashlib.sha256(token.encode()).hexdigest()


# Singleton instance
token_service = TokenService()
