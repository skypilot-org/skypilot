# Immediate Service Account Code Improvements

## Quick Wins (Can be implemented immediately)

### 1. Extract Common Permission Check Function

**Current Issue**: Permission checks are duplicated across endpoints in `sky/users/server.py`

**Solution**: Create a reusable permission checker function

```python
# Add to sky/users/server.py
def _check_service_account_token_access(auth_user: models.User, 
                                      token_info: Dict[str, Any], 
                                      action: str) -> None:
    """Check if user has permission to access service account token.
    
    Raises HTTPException if access denied.
    """
    if not permission.permission_service.check_service_account_token_permission(
            auth_user.id, token_info['creator_user_hash'], action):
        raise fastapi.HTTPException(
            status_code=403,
            detail=f'You can only {action} your own service accounts. '
                   f'Only admins can {action} service accounts owned by other users.')

def _get_token_info_or_404(token_id: str) -> Dict[str, Any]:
    """Get token info or raise 404 if not found."""
    token_info = global_user_state.get_service_account_token(token_id)
    if token_info is None:
        raise fastapi.HTTPException(status_code=404, detail='Token not found')
    return token_info
```

**Usage**: Replace repeated code in endpoints with these functions.

### 2. Consolidate Token Validation Logic

**Current Issue**: Token validation is scattered across multiple files

**Solution**: Create a centralized validation function

```python
# Add to sky/users/token_service.py or create sky/users/validation.py
def validate_service_account_token_request(token_name: str, 
                                         expires_in_days: Optional[int]) -> None:
    """Validate service account token creation request."""
    if not token_name or not token_name.strip():
        raise ValueError('Token name is required')
    
    if expires_in_days is not None and expires_in_days < 0:
        raise ValueError('Expiration days must be positive or 0 for never expire')

def validate_token_format(token: str) -> bool:
    """Validate service account token format."""
    return isinstance(token, str) and token.startswith('sky_') and len(token) > 10
```

### 3. Create Service Account Token Info Builder

**Current Issue**: Token info building is duplicated in `get_service_account_tokens`

**Solution**: Extract to a reusable function

```python
# Add to sky/users/server.py
def _build_token_info(token: Dict[str, Any]) -> Dict[str, Any]:
    """Build enriched token info for API responses."""
    token_info = {
        'token_id': token['token_id'],
        'token_name': token['token_name'],
        'created_at': token['created_at'],
        'last_used_at': token['last_used_at'],
        'expires_at': token['expires_at'],
        'creator_user_hash': token['creator_user_hash'],
        'service_account_user_id': token['service_account_user_id'],
    }

    # Add creator display name
    creator_user = global_user_state.get_user(token['creator_user_hash'])
    token_info['creator_name'] = creator_user.name if creator_user else 'Unknown'

    # Add service account name
    sa_user = global_user_state.get_user(token['service_account_user_id'])
    token_info['service_account_name'] = (sa_user.name if sa_user else token['token_name'])

    # Add service account roles
    roles = permission.permission_service.get_user_roles(token['service_account_user_id'])
    token_info['service_account_roles'] = roles

    return token_info
```

### 4. Improve Error Handling Consistency

**Current Issue**: Inconsistent error handling patterns across endpoints

**Solution**: Create error handling decorators

```python
# Add to sky/users/server.py or create sky/users/error_handlers.py
def handle_service_account_errors(func):
    """Decorator for consistent error handling in service account endpoints."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except ValueError as e:
            raise fastapi.HTTPException(status_code=400, detail=str(e))
        except PermissionError as e:
            raise fastapi.HTTPException(status_code=403, detail=str(e))
        except Exception as e:
            logger.error(f'Service account operation failed: {e}', exc_info=True)
            raise fastapi.HTTPException(
                status_code=500, 
                detail=f'Service account operation failed: {str(e)}'
            )
    return wrapper
```

### 5. Extract Service Account ID Generation

**Current Issue**: Service account ID generation logic is in the endpoint

**Solution**: Move to a dedicated utility function

```python
# Add to sky/models.py or create sky/users/service_account_utils.py
import secrets
import re

def generate_service_account_user_id(token_name: str, creator_user_id: str) -> str:
    """Generate a unique user ID for a service account.
    
    Creates a deterministic yet unique ID based on creator and token name
    with added randomness to avoid collisions.
    """
    random_suffix = secrets.token_hex(8)  # 16 character hex string
    
    # Clean token name: remove special chars, limit length
    safe_token_name = re.sub(r'[^a-zA-Z0-9]', '', token_name)[:8]
    creator_prefix = creator_user_id[:8]  # First 8 chars of creator ID
    
    service_account_id = f'sa-{creator_prefix}-{safe_token_name}-{random_suffix}'
    return service_account_id
```

### 6. Add Service Account Token Caching

**Current Issue**: Service account headers are recalculated on every request

**Solution**: Add simple caching to client authentication

```python
# Modify sky/client/service_account_auth.py
_token_cache: Optional[str] = None
_headers_cache: Optional[dict] = None

def get_service_account_token() -> Optional[str]:
    """Get service account token with basic caching."""
    global _token_cache
    
    if _token_cache is not None:
        return _token_cache
    
    # Existing logic...
    token = _load_token_from_sources()
    _token_cache = token
    return token

def get_service_account_headers() -> dict:
    """Get HTTP headers with caching."""
    global _headers_cache
    
    if _headers_cache is not None:
        return _headers_cache
    
    token = get_service_account_token()
    if token:
        _headers_cache = {
            'Authorization': f'Bearer {token}',
            'X-Service-Account-Auth': 'true'
        }
        return _headers_cache
    return {}

def clear_service_account_cache():
    """Clear service account authentication cache."""
    global _token_cache, _headers_cache
    _token_cache = None
    _headers_cache = None
```

### 7. Consolidate Constants

**Current Issue**: Magic strings and values scattered across files

**Solution**: Create constants file

```python
# Create sky/users/service_account_constants.py
class ServiceAccountConstants:
    """Constants for service account operations."""
    
    # Token validation
    TOKEN_PREFIX = 'sky_'
    MIN_TOKEN_LENGTH = 10
    DEFAULT_EXPIRATION_DAYS = 30
    
    # Service account ID format
    SA_ID_PREFIX = 'sa-'
    CREATOR_PREFIX_LENGTH = 8
    TOKEN_NAME_MAX_LENGTH = 8
    RANDOM_SUFFIX_LENGTH = 8
    
    # Permission actions
    ACTION_CREATE = 'create'
    ACTION_DELETE = 'delete'
    ACTION_VIEW = 'view'
    ACTION_UPDATE = 'update'
    ACTION_ROTATE = 'rotate'
    
    # Headers
    AUTHORIZATION_HEADER = 'Authorization'
    SERVICE_ACCOUNT_HEADER = 'X-Service-Account-Auth'
    BEARER_PREFIX = 'Bearer '
    
    # Environment variables
    ENV_SKYPILOT_TOKEN = 'SKYPILOT_TOKEN'
    ENV_ENABLE_SERVICE_ACCOUNTS = 'SKYPILOT_ENABLE_SERVICE_ACCOUNTS'
```

### 8. Improve Service Account Model

**Current Issue**: Basic ServiceAccountToken model lacks useful methods

**Solution**: Enhance the existing model

```python
# Modify sky/models.py
@dataclasses.dataclass
class ServiceAccountToken:
    """Enhanced service account token information."""
    token_id: str
    user_hash: str
    token_name: str
    token_hash: str
    created_at: int
    last_used_at: Optional[int] = None
    expires_at: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            'token_id': self.token_id,
            'user_hash': self.user_hash,
            'token_name': self.token_name,
            'created_at': self.created_at,
            'last_used_at': self.last_used_at,
            'expires_at': self.expires_at,
        }

    def is_expired(self) -> bool:
        """Check if the token is expired."""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at
    
    def days_until_expiration(self) -> Optional[int]:
        """Get days until token expires (None if never expires)."""
        if self.expires_at is None:
            return None
        
        remaining_seconds = self.expires_at - time.time()
        if remaining_seconds <= 0:
            return 0
        
        return int(remaining_seconds / (24 * 3600))
    
    def is_about_to_expire(self, warning_days: int = 7) -> bool:
        """Check if token expires within warning period."""
        days_left = self.days_until_expiration()
        if days_left is None:
            return False
        return days_left <= warning_days
    
    @property
    def creator_user_hash(self) -> str:
        """Alias for user_hash for consistency."""
        return self.user_hash
```

### 9. Add Request/Response Models

**Current Issue**: Using generic Dict[str, Any] for responses

**Solution**: Create proper Pydantic models

```python
# Add to sky/server/requests/payloads.py or create new response models file
from typing import List

class ServiceAccountTokenResponse(BaseModel):
    """Response model for service account token operations."""
    token_id: str
    token_name: str
    created_at: int
    last_used_at: Optional[int]
    expires_at: Optional[int]
    creator_user_hash: str
    creator_name: str
    service_account_user_id: str
    service_account_name: str
    service_account_roles: List[str]

class ServiceAccountTokenCreateResponse(BaseModel):
    """Response model for token creation."""
    token_id: str
    token_name: str
    token: str  # The actual JWT token
    expires_at: Optional[int]
    service_account_user_id: str
    creator_user_id: str
    message: str

class ServiceAccountTokenListResponse(BaseModel):
    """Response model for token listing."""
    tokens: List[ServiceAccountTokenResponse]
    total_count: int
```

### 10. Simplify Database Error Handling

**Current Issue**: Database errors not properly categorized

**Solution**: Add database operation wrappers

```python
# Add to sky/global_user_state.py
def safe_db_operation(operation_name: str):
    """Decorator for safe database operations with proper error handling."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f'Database operation {operation_name} failed: {e}')
                raise DatabaseOperationError(f'{operation_name} failed: {str(e)}') from e
        return wrapper
    return decorator

class DatabaseOperationError(Exception):
    """Exception for database operation failures."""
    pass
```

## Implementation Priority

1. **High Priority** (Immediate impact, low risk):
   - Extract common permission check function (#1)
   - Consolidate token validation logic (#2) 
   - Create constants file (#7)

2. **Medium Priority** (Good improvements, moderate effort):
   - Create token info builder (#3)
   - Improve error handling consistency (#4)
   - Extract service account ID generation (#5)

3. **Low Priority** (Nice to have, requires more testing):
   - Add caching (#6)
   - Enhance models (#8)
   - Add request/response models (#9)
   - Database error handling (#10)

## Benefits of Immediate Improvements

- **Reduced Code Duplication**: Functions can be reused across endpoints
- **Better Error Handling**: Consistent error messages and status codes
- **Improved Maintainability**: Common logic centralized in reusable functions
- **Enhanced Performance**: Basic caching reduces repeated computations
- **Better Type Safety**: Proper models instead of generic dictionaries
- **Easier Testing**: Smaller, focused functions are easier to unit test

These improvements can be implemented incrementally without breaking existing functionality, making them ideal first steps toward the larger refactoring goals.