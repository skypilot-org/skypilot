# Service Account Refactoring Proposal

## Current State Analysis

The current service account implementation spans multiple files and has several areas for improvement:

### Existing Architecture
- **Database Operations**: Direct SQL in `global_user_state.py` 
- **API Endpoints**: All CRUD operations in `sky/users/server.py`
- **Token Management**: JWT logic in `sky/users/token_service.py`
- **Client Auth**: Helpers in `sky/client/service_account_auth.py`
- **Middleware**: Bearer token validation in `sky/server/server.py`
- **Models**: Basic dataclass in `sky/models.py`
- **Permissions**: Scattered permission checks across endpoints

### Issues with Current Implementation
1. **Code Duplication**: Repeated permission checks across endpoints
2. **Mixed Concerns**: Database operations mixed with business logic
3. **Large Files**: `sky/users/server.py` contains too many responsibilities
4. **Inconsistent Error Handling**: Different error patterns across endpoints
5. **Limited Models**: Insufficient object-oriented models for service accounts
6. **Scattered Logic**: Service account logic spread across multiple files

## Proposed Refactoring

### 1. Create Dedicated Service Account Module Structure

```
sky/users/service_accounts/
├── __init__.py
├── models.py           # Enhanced models with business logic
├── service.py          # Core business logic service
├── repository.py       # Database operations abstraction
├── permissions.py      # Consolidated permission logic
├── router.py          # API endpoints (extracted from server.py)
└── exceptions.py      # Service account specific exceptions
```

### 2. Enhanced Models (`sky/users/service_accounts/models.py`)

```python
@dataclasses.dataclass
class ServiceAccount:
    """Enhanced ServiceAccount model with business logic."""
    service_account_id: str
    service_account_name: str
    creator_user_hash: str
    created_at: int
    creator_user: Optional['User'] = None
    
    @classmethod
    def generate_service_account_id(cls, token_name: str, creator_user_id: str) -> str:
        """Generate deterministic service account ID."""
        # Move logic from _generate_service_account_user_id here
        
    @property
    def creator_user_hash(self) -> str:
        """Get creator user hash."""
        
    @property
    def service_account_user_id(self) -> str:
        """Alias for service_account_id for backward compatibility."""

@dataclasses.dataclass  
class ServiceAccountToken:
    """Enhanced ServiceAccountToken model."""
    token_id: str
    token_name: str
    token_hash: str
    service_account: ServiceAccount
    created_at: int
    last_used_at: Optional[int] = None
    expires_at: Optional[int] = None
    
    def is_expired(self) -> bool:
        """Check if token is expired."""
        
    def is_about_to_expire(self, warning_days: int = 7) -> bool:
        """Check if token expires within warning period."""
        
    @property
    def creator_user_hash(self) -> str:
        """Get creator user hash from service account."""
        return self.service_account.creator_user_hash
        
    @property 
    def service_account_user_id(self) -> str:
        """Get service account user ID."""
        return self.service_account.service_account_id

@dataclasses.dataclass
class ServiceAccountTokenCreateRequest:
    """Request model for token creation."""
    token_name: str
    expires_in_days: Optional[int] = None
    
    def validate(self) -> None:
        """Validate creation request."""
```

### 3. Repository Pattern (`sky/users/service_accounts/repository.py`)

```python
class ServiceAccountRepository:
    """Repository for service account database operations."""
    
    def create_service_account(self, service_account: ServiceAccount) -> ServiceAccount:
        """Create new service account."""
        
    def get_service_account_by_id(self, service_account_id: str) -> Optional[ServiceAccount]:
        """Get service account by ID."""
        
    def create_service_account_token(self, token: ServiceAccountToken) -> ServiceAccountToken:
        """Create new service account token."""
        
    def get_service_account_token(self, token_id: str) -> Optional[ServiceAccountToken]:
        """Get service account token with related service account."""
        
    def get_all_service_account_tokens(self) -> List[ServiceAccountToken]:
        """Get all tokens with service account information."""
        
    def get_user_service_account_tokens(self, user_hash: str) -> List[ServiceAccountToken]:
        """Get tokens created by specific user."""
        
    def update_token_last_used(self, token_id: str) -> None:
        """Update token last used timestamp."""
        
    def delete_service_account_token(self, token_id: str) -> bool:
        """Delete service account token."""
        
    def rotate_service_account_token(self, token_id: str, new_token_hash: str, 
                                   new_expires_at: Optional[int]) -> None:
        """Rotate service account token."""

# Factory function to get repository instance
def get_service_account_repository() -> ServiceAccountRepository:
    """Get service account repository instance."""
    return ServiceAccountRepository()
```

### 4. Business Logic Service (`sky/users/service_accounts/service.py`)

```python
class ServiceAccountService:
    """Core business logic for service accounts."""
    
    def __init__(self, repository: ServiceAccountRepository, 
                 token_service: 'TokenService',
                 permission_service: 'PermissionService'):
        self.repository = repository
        self.token_service = token_service
        self.permission_service = permission_service
    
    def create_service_account_token(self, request: ServiceAccountTokenCreateRequest,
                                   creator_user: User) -> ServiceAccountTokenResult:
        """Create new service account token with full business logic."""
        # Validate request
        request.validate()
        
        # Generate service account
        service_account_id = ServiceAccount.generate_service_account_id(
            request.token_name, creator_user.id)
        
        service_account = ServiceAccount(
            service_account_id=service_account_id,
            service_account_name=request.token_name,
            creator_user_hash=creator_user.id,
            created_at=int(time.time())
        )
        
        # Create user entry and permissions
        self._setup_service_account_user(service_account)
        
        # Create JWT token
        token_data = self.token_service.create_token(...)
        
        # Create and store token
        token = ServiceAccountToken(...)
        created_token = self.repository.create_service_account_token(token)
        
        return ServiceAccountTokenResult(token=created_token, jwt_token=token_data['token'])
    
    def delete_service_account_token(self, token_id: str, requesting_user: User) -> None:
        """Delete service account token with permission checks."""
        token = self.repository.get_service_account_token(token_id)
        if not token:
            raise ServiceAccountTokenNotFound(token_id)
            
        if not self._check_token_permission(requesting_user, token, 'delete'):
            raise InsufficientPermissions("Cannot delete this token")
            
        self.repository.delete_service_account_token(token_id)
    
    def get_service_account_tokens(self, requesting_user: User) -> List[ServiceAccountTokenInfo]:
        """Get service account tokens with enriched information."""
        tokens = self.repository.get_all_service_account_tokens()
        
        result = []
        for token in tokens:
            # Enrich with creator info, roles, etc.
            token_info = self._enrich_token_info(token)
            result.append(token_info)
            
        return result
    
    def _check_token_permission(self, user: User, token: ServiceAccountToken, action: str) -> bool:
        """Consolidated permission checking logic."""
        return self.permission_service.check_service_account_token_permission(
            user.id, token.creator_user_hash, action)
    
    def _enrich_token_info(self, token: ServiceAccountToken) -> ServiceAccountTokenInfo:
        """Add additional information to token for API responses."""
        # Get creator name, roles, etc.
        pass

@dataclasses.dataclass
class ServiceAccountTokenResult:
    """Result of token creation operation."""
    token: ServiceAccountToken
    jwt_token: str  # The actual JWT token to return to user
    
@dataclasses.dataclass  
class ServiceAccountTokenInfo:
    """Enriched token information for API responses."""
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
```

### 5. Consolidated Permissions (`sky/users/service_accounts/permissions.py`)

```python
class ServiceAccountPermissions:
    """Consolidated permission logic for service accounts."""
    
    def __init__(self, permission_service: 'PermissionService'):
        self.permission_service = permission_service
    
    def can_create_token(self, user: User) -> bool:
        """Check if user can create service account tokens."""
        # All authenticated users can create tokens
        return True
    
    def can_manage_token(self, user: User, token: ServiceAccountToken, action: str) -> bool:
        """Check if user can perform action on token."""
        return self.permission_service.check_service_account_token_permission(
            user.id, token.creator_user_hash, action)
    
    def can_view_all_tokens(self, user: User) -> bool:
        """Check if user can view all service account tokens."""
        roles = self.permission_service.get_user_roles(user.id)
        return RoleName.ADMIN.value in roles
    
    def get_accessible_tokens(self, user: User, all_tokens: List[ServiceAccountToken]) -> List[ServiceAccountToken]:
        """Filter tokens based on user permissions."""
        if self.can_view_all_tokens(user):
            return all_tokens
        
        # Return only user's own tokens
        return [token for token in all_tokens if token.creator_user_hash == user.id]

def require_service_account_permission(action: str):
    """Decorator for permission checking in endpoints."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract request and token_id from args/kwargs
            # Perform permission check
            # Call original function if authorized
            pass
        return wrapper
    return decorator
```

### 6. Custom Exceptions (`sky/users/service_accounts/exceptions.py`)

```python
class ServiceAccountError(Exception):
    """Base exception for service account operations."""
    pass

class ServiceAccountTokenNotFound(ServiceAccountError):
    """Token not found exception."""
    def __init__(self, token_id: str):
        super().__init__(f"Service account token {token_id} not found")
        self.token_id = token_id

class InsufficientPermissions(ServiceAccountError):
    """Insufficient permissions exception."""
    pass

class InvalidTokenRequest(ServiceAccountError):
    """Invalid token creation request."""
    pass

class TokenExpired(ServiceAccountError):
    """Token has expired."""
    pass
```

### 7. Simplified API Router (`sky/users/service_accounts/router.py`)

```python
# Extract service account endpoints from server.py
router = fastapi.APIRouter(prefix='/service-account-tokens', tags=['service-accounts'])

# Dependency injection
def get_service_account_service() -> ServiceAccountService:
    """Get service account service instance."""
    repository = get_service_account_repository()
    return ServiceAccountService(repository, token_service, permission_service)

@router.get('/')
async def get_service_account_tokens(
    request: fastapi.Request,
    service: ServiceAccountService = fastapi.Depends(get_service_account_service)
) -> List[ServiceAccountTokenInfo]:
    """Get service account tokens with proper service layer."""
    auth_user = request.state.auth_user
    if not auth_user:
        raise fastapi.HTTPException(status_code=401, detail='Authentication required')
    
    return service.get_service_account_tokens(auth_user)

@router.post('/')
async def create_service_account_token(
    request: fastapi.Request,
    token_body: ServiceAccountTokenCreateRequest,
    service: ServiceAccountService = fastapi.Depends(get_service_account_service)
) -> ServiceAccountTokenResult:
    """Create service account token with proper validation."""
    auth_user = request.state.auth_user
    if not auth_user:
        raise fastapi.HTTPException(status_code=401, detail='Authentication required')
    
    try:
        return service.create_service_account_token(token_body, auth_user)
    except InvalidTokenRequest as e:
        raise fastapi.HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f'Failed to create service account token: {e}')
        raise fastapi.HTTPException(status_code=500, detail='Failed to create service account token')

# Similar pattern for other endpoints...
```

### 8. Enhanced Client Authentication (`sky/client/service_account_auth.py`)

```python
class ServiceAccountAuthManager:
    """Manager for service account authentication operations."""
    
    def __init__(self):
        self._token_cache: Optional[str] = None
        self._headers_cache: Optional[dict] = None
    
    def get_service_account_token(self) -> Optional[str]:
        """Get service account token with caching."""
        if self._token_cache:
            return self._token_cache
            
        # Existing logic but with caching
        self._token_cache = self._load_token()
        return self._token_cache
    
    def get_service_account_headers(self) -> dict:
        """Get headers with caching."""
        if self._headers_cache:
            return self._headers_cache
            
        token = self.get_service_account_token()
        if token:
            self._headers_cache = {
                'Authorization': f'Bearer {token}',
                'X-Service-Account-Auth': 'true'
            }
            return self._headers_cache
        return {}
    
    def clear_cache(self) -> None:
        """Clear authentication cache."""
        self._token_cache = None
        self._headers_cache = None
    
    def validate_token_format(self, token: str) -> bool:
        """Validate token format."""
        return token.startswith('sky_') and len(token) > 10

# Global instance
auth_manager = ServiceAccountAuthManager()

# Update existing functions to use manager
def get_service_account_token() -> Optional[str]:
    return auth_manager.get_service_account_token()

def get_service_account_headers() -> dict:
    return auth_manager.get_service_account_headers()
```

## Migration Plan

### Phase 1: Extract Models and Repository
1. Create new service account module structure
2. Move and enhance models
3. Create repository layer
4. Update existing code to use new models (backward compatibility)

### Phase 2: Extract Business Logic
1. Create service layer
2. Move business logic from endpoints to service
3. Implement proper error handling
4. Add comprehensive tests

### Phase 3: Refactor API Endpoints  
1. Extract endpoints to dedicated router
2. Implement dependency injection
3. Simplify endpoint logic (delegate to service layer)
4. Update error handling

### Phase 4: Enhance Client Authentication
1. Implement authentication manager
2. Add caching and validation
3. Improve error handling

### Phase 5: Cleanup and Optimization
1. Remove old code
2. Update documentation
3. Performance optimization
4. Security review

## Benefits of This Refactoring

1. **Better Separation of Concerns**: Clear boundaries between data access, business logic, and API layers
2. **Improved Testability**: Each component can be tested independently
3. **Reduced Code Duplication**: Common patterns extracted to reusable components
4. **Enhanced Maintainability**: Cleaner, more organized code structure
5. **Better Error Handling**: Consistent error patterns across the system
6. **Improved Performance**: Caching and optimized database operations
7. **Enhanced Security**: Centralized permission checking and validation
8. **Better Documentation**: Self-documenting code with clear interfaces

## Backward Compatibility

- All existing API endpoints maintain the same interface
- Database schema remains unchanged
- Existing client code continues to work
- Legacy functions marked as deprecated but functional

This refactoring transforms the service account functionality from scattered, procedural code into a well-organized, object-oriented system that follows best practices for maintainability and extensibility.