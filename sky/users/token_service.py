"""JWT-based service account token management for SkyPilot."""

import datetime
import hashlib
import secrets
import threading
import time
from typing import Any, Dict, Optional

import jwt

from sky import global_user_state
from sky import sky_logging
from sky.utils.db import retries as db_retries

logger = sky_logging.init_logger(__name__)

# JWT Configuration
JWT_ALGORITHM = 'HS256'
JWT_ISSUER = 'sky'  # Shortened for compact tokens
JWT_SECRET_DB_KEY = 'jwt_secret'

# Deliberately below `db_retries` default of 5. This read happens on the
# request path, and the deadline in `db_lookup.call_with_deadline` releases the
# caller but never the executor thread, so every extra attempt is another
# multiple of the DB layer's own timeout that the thread stays held. Two
# attempts ride out a single blip without turning one lookup into minutes.
_SECRET_READ_MAX_RETRIES = 2

# How long a thread waits for another thread's in-flight secret load. A healthy
# load is one indexed query, so a waiting burst after a restart still gets
# through; a load stuck on a degraded database releases its waiters here
# instead of parking them on the lock for its whole duration, which would
# convoy the auth executor.
_SECRET_LOAD_LOCK_TIMEOUT_SECONDS = 2.0


class JWTSecretUnavailableError(Exception):
    """The JWT signing secret could not be loaded from the database."""


def _read_secret_from_db() -> Optional[str]:
    """The persisted JWT secret, or None when the row does not exist.

    Raises on a database failure. A failed read must never be reported as
    "no secret exists yet": the caller would mint a replacement, and every
    token already issued would stop verifying.
    """
    return db_retries.with_db_retries(
        lambda _attempt: global_user_state.get_system_config(JWT_SECRET_DB_KEY),
        max_retries=_SECRET_READ_MAX_RETRIES)


def _validated_secret(secret: str) -> str:
    """Return `secret`, refusing an empty stored value.

    An empty value is corruption and neither of the two cases the bootstrap
    handles: generating over it would clobber whatever is really meant to be
    there, and using it hands PyJWT a key it rejects (`InvalidKeyError`) on
    every request. That is not an `InvalidTokenError`, so it escapes
    `verify_token` and surfaces as a misleading 401.

    Applied at each point a value is obtained rather than once at the end, so
    a value about to be refused is never first announced as a healthy secret.
    """
    if not secret:
        raise ValueError(f'System config {JWT_SECRET_DB_KEY!r} holds an empty '
                         'value. Restore or delete the row to let the server '
                         'bootstrap a secret.')
    return secret


def _warn_if_tokens_predate_new_secret() -> None:
    """Flag a freshly generated secret that orphans existing tokens.

    Generating the secret on a new deployment is routine. Generating one while
    token rows already exist means those tokens can no longer be verified,
    which should be impossible and is worth an alertable log line.
    """
    try:
        # A count, not the rows: this runs while holding the load lock and an
        # auth executor thread, against a database that may be degraded.
        count = global_user_state.count_service_account_tokens()
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Generated a new JWT signing secret; could not check '
                       f'whether service account tokens predate it: {e}')
        return
    if count:
        logger.error(
            f'Generated a NEW JWT signing secret while {count} service '
            f'account token(s) already exist. Those tokens can no longer be '
            f'verified and have to be rotated. This means the previous secret '
            f'was lost from the database.')
    else:
        logger.info('Generated the JWT signing secret and stored it in the '
                    'database. It persists across API server restarts.')


class TokenService:
    """Service for managing JWT-based service account tokens."""

    def __init__(self):
        self.secret_key = None
        self.init_lock = threading.Lock()

    def secret_loaded(self) -> bool:
        """Whether the signing secret is already in memory.

        Lets the request path skip dispatching `ensure_secret_loaded` to the
        auth executor in the steady state. `secret_key` is assigned once and
        never reassigned, so reading it unlocked is safe.
        """
        return self.secret_key is not None

    def ensure_secret_loaded(self) -> None:
        """Load the signing secret, generating it on a new deployment.

        Blocks on the database. Callers on the request path should run this
        off the event loop under a deadline before reaching `verify_token`,
        which is then pure CPU work.

        Raises:
            JWTSecretUnavailableError: the secret could not be loaded. Nothing
                is cached, so a later call retries.
        """
        self._lazy_initialize()

    def _lazy_initialize(self):
        if self.secret_key is not None:
            return
        if not self.init_lock.acquire(
                timeout=_SECRET_LOAD_LOCK_TIMEOUT_SECONDS):
            # Re-check before giving up: the holder may have finished within
            # the last instant of the wait, and failing a caller whose secret
            # is now loaded would 503 a request that can be served.
            if self.secret_key is not None:
                return
            raise JWTSecretUnavailableError(
                f'Another request is still loading the JWT signing secret '
                f'after {_SECRET_LOAD_LOCK_TIMEOUT_SECONDS}s, which means the '
                f'server database is not answering.')
        try:
            if self.secret_key is not None:
                return
            self.secret_key = self._get_or_generate_secret()
        finally:
            self.init_lock.release()

    def _get_or_generate_secret(self) -> str:
        """Get the JWT secret from the database, generating it if absent.

        Generation happens only after a *successful* read found no row. A read
        that raised is propagated: treating it as "absent" would overwrite the
        live secret and invalidate every token already issued. The write is
        insert-if-absent for the same reason, so a racing replica adopts the
        stored secret instead of clobbering it.
        """
        try:
            secret = _read_secret_from_db()
            if secret is None:
                candidate = secrets.token_urlsafe(64)
                secret = _validated_secret(
                    global_user_state.get_or_set_system_config(
                        JWT_SECRET_DB_KEY, candidate))
                if secret == candidate:
                    _warn_if_tokens_predate_new_secret()
                else:
                    logger.info('Adopted the JWT signing secret stored '
                                'concurrently by another API server.')
            else:
                secret = _validated_secret(secret)
                logger.debug('Retrieved existing JWT secret from database')
        except Exception as e:  # pylint: disable=broad-except
            # Never fall back to an in-memory secret: this process would sign
            # tokens nothing else -- including itself after a restart -- can
            # verify.
            logger.error(f'Failed to load the JWT signing secret: {e}',
                         exc_info=True)
            raise JWTSecretUnavailableError(
                'The JWT signing secret could not be loaded from the server '
                'database.') from e
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

        # Add expiration if specified. Write both 'e' (legacy short name)
        # and the RFC 7519 standard 'exp' so PyJWT's automatic
        # ExpiredSignatureError path enforces it without our manual check.
        # The manual 'e' check in verify_token stays for backward compat
        # with tokens issued before this change; once those have expired
        # the manual check can be deleted.
        expires_at = None
        if expires_in_days:
            exp_time = now + datetime.timedelta(days=expires_in_days)
            exp_ts = int(exp_time.timestamp())
            payload['e'] = exp_ts
            payload['exp'] = exp_ts
            expires_at = exp_ts

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

            # Manually verify expiration for tokens issued before we
            # started writing the standard 'exp' claim alongside 'e'.
            # New tokens carry both, and PyJWT's jwt.decode above will
            # have already raised ExpiredSignatureError on 'exp'. This
            # branch only matters for legacy tokens with 'e' only; it
            # can be removed once those have all expired.
            exp = payload.get('e')
            if exp is not None and exp < int(time.time()):
                logger.warning('Token has expired')
                return None

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
