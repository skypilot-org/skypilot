"""JWT-based service account token management for SkyPilot."""

import contextlib
import datetime
import hashlib
import os
import secrets
import threading
from typing import Any, Dict, Generator, Optional

import filelock
import jwt

from sky import global_user_state
from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# JWT Configuration
JWT_ALGORITHM = 'HS256'
JWT_ISSUER = 'sky'  # Shortened for compact tokens
JWT_SECRET_DB_KEY = 'jwt_secret'

# File lock for JWT secret initialization
JWT_SECRET_LOCK_PATH = os.path.expanduser('~/.sky/.jwt_secret_init.lock')
JWT_SECRET_LOCK_TIMEOUT_SECONDS = 20


@contextlib.contextmanager
def _jwt_secret_lock() -> Generator[None, None, None]:
    """Context manager for JWT secret initialization lock."""
    try:
        with filelock.FileLock(JWT_SECRET_LOCK_PATH,
                               JWT_SECRET_LOCK_TIMEOUT_SECONDS):
            yield
    except filelock.Timeout as e:
        raise RuntimeError(f'Failed to initialize JWT secret due to a timeout '
                           f'when trying to acquire the lock at '
                           f'{JWT_SECRET_LOCK_PATH}. '
                           'Please try again or manually remove the lock '
                           f'file if you believe it is stale.') from e


class TokenService:
    """Service for managing JWT-based service account tokens."""

    def __init__(self):
        self.secret_key = None
        self.init_lock = threading.Lock()

    def _lazy_initialize(self):
        if self.secret_key is not None:
            return
        with self.init_lock:
            if self.secret_key is not None:
                return
            self.secret_key = self._get_or_generate_secret()

    def _get_or_generate_secret(self) -> str:
        """Get JWT secret from database or generate a new one."""

        def _get_secret_from_db():
            try:
                db_secret = global_user_state.get_system_config(
                    JWT_SECRET_DB_KEY)
                if db_secret:
                    logger.debug('Retrieved existing JWT secret from database')
                    return db_secret
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(f'Failed to get JWT secret from database: {e}')
            return None

        # Try to get from database (persistent across deployments)
        token_from_db = _get_secret_from_db()
        if token_from_db:
            return token_from_db

        with _jwt_secret_lock():
            token_from_db = _get_secret_from_db()
            if token_from_db:
                return token_from_db
            # Generate a new secret and store in database
            new_secret = secrets.token_urlsafe(64)
            try:
                global_user_state.set_system_config(JWT_SECRET_DB_KEY,
                                                    new_secret)
                logger.info(
                    'Generated new JWT secret and stored in database. '
                    'This secret will persist across API server restarts.')
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    f'Failed to store new JWT secret in database: {e}. '
                    f'Using in-memory secret (tokens will not persist '
                    f'across restarts).')

            return new_secret

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
        self._lazy_initialize()
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
        self._lazy_initialize()
        if not token.startswith('sky_'):
            return None

        # Remove the sky_ prefix
        jwt_token = token[4:]

        try:
            # Decode and verify JWT (without issuer verification)
            payload = jwt.decode(jwt_token,
                                 self.secret_key,
                                 algorithms=[JWT_ALGORITHM])

            # Manually verify issuer using our shortened field name
            token_issuer = payload.get('i')
            if token_issuer != JWT_ISSUER:
                logger.warning(f'Invalid token issuer: {token_issuer}')
                return None

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
            logger.warning('Token has expired')
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f'Invalid token: {e}')
            return None


# Singleton instance
token_service = TokenService()
