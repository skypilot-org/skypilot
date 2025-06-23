#!/usr/bin/env python3
"""
Service Account Token Demo for SkyPilot

This script demonstrates how to use service account tokens for automated
API access without going through SSO authentication.

Prerequisites:
1. SkyPilot API server deployed with enableUserManagement=true and enableServiceAccountTokens=true
2. A valid user account with authentication credentials

OAuth2 Proxy Integration:
When OAuth2 proxy is enabled (for Okta/SSO), service account tokens automatically
use the /sa/* path to bypass the OAuth2 proxy. This script handles this transparently.

Usage:
    # Initial setup (creates token)
    python service_account_demo.py --endpoint https://your-skypilot-api.com --username user --password pass
    
    # Test existing token
    python service_account_demo.py --endpoint https://your-skypilot-api.com --token sky_abc123... --mode test
"""

import argparse
import json
import requests
import sys
import time
from typing import Dict, Any, Optional


class SkyPilotAPIClient:
    """A simple client for interacting with SkyPilot API using service account tokens."""
    
    def __init__(self, endpoint: str, token: Optional[str] = None):
        self.endpoint = endpoint.rstrip('/')
        self.token = token
        self.session = requests.Session()
        self.use_service_account_path = False
        
        if self.token:
            self.session.headers.update({
                'Authorization': f'Bearer {self.token}',
                'Content-Type': 'application/json'
            })
            # When using bearer tokens, check if we need to use /sa/ path for OAuth2 bypass
            self.use_service_account_path = True
    
    def _get_api_url(self, path: str) -> str:
        """Get the correct API URL based on authentication method."""
        if self.use_service_account_path and self.token:
            # Use /sa/ prefix for service account tokens (OAuth2 proxy bypass)
            if path.startswith('/'):
                path = path[1:]  # Remove leading slash
            return f'{self.endpoint}/sa/{path}'
        else:
            # Regular path for basic auth or no OAuth2 proxy
            return f'{self.endpoint}/{path}' if not path.startswith('/') else f'{self.endpoint}{path}'
    
    def health_check(self) -> Dict[str, Any]:
        """Check API server health and feature status."""
        response = self.session.get(self._get_api_url('/api/health'))
        response.raise_for_status()
        return response.json()
    
    def create_service_account_token(self, token_name: str, expires_in_days: Optional[int] = None) -> Dict[str, Any]:
        """Create a new service account token."""
        data = {'token_name': token_name}
        if expires_in_days:
            data['expires_in_days'] = expires_in_days
            
        response = self.session.post(
            self._get_api_url('/api/v1/users/service-account-tokens'),
            json=data
        )
        response.raise_for_status()
        return response.json()
    
    def list_service_account_tokens(self) -> list:
        """List all service account tokens for the current user."""
        response = self.session.get(self._get_api_url('/api/v1/users/service-account-tokens'))
        response.raise_for_status()
        return response.json()
    
    def delete_service_account_token(self, token_id: str) -> Dict[str, str]:
        """Delete a service account token."""
        response = self.session.delete(
            self._get_api_url('/api/v1/users/service-account-tokens'),
            json={'token_id': token_id}
        )
        response.raise_for_status()
        return response.json()
    
    def get_cluster_status(self) -> list:
        """Get status of all clusters."""
        response = self.session.post(
            self._get_api_url('/api/v1/status'),
            json={}
        )
        response.raise_for_status()
        return response.json()


def demo_basic_flow(endpoint: str, username: str, password: str):
    """Demonstrate the basic service account token workflow."""
    print("🚀 SkyPilot Service Account Token Demo")
    print("=" * 50)
    
    # Step 1: Create client with basic auth to bootstrap
    print("\n1️⃣ Checking API server health...")
    client = SkyPilotAPIClient(endpoint)
    client.session.auth = (username, password)  # Use basic auth initially
    
    try:
        health = client.health_check()
        print(f"✅ API server is healthy")
        print(f"   Version: {health.get('version', 'unknown')}")
        print(f"   Basic auth enabled: {health.get('basic_auth_enabled', False)}")
        print(f"   Service account tokens enabled: {health.get('service_account_tokens_enabled', False)}")
        
        if not health.get('service_account_tokens_enabled', False):
            print("❌ Service account tokens are not enabled on this server")
            return
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to connect to API server: {e}")
        return
    
    # Step 2: Create a service account token
    print("\n2️⃣ Creating service account token...")
    try:
        token_name = f"demo-token-{int(time.time())}"
        token_data = client.create_service_account_token(token_name, expires_in_days=30)
        print(f"✅ Created token: {token_data['token_name']}")
        print(f"   Token ID: {token_data['token_id']}")
        print(f"   Expires: {time.ctime(token_data['expires_at']) if token_data.get('expires_at') else 'Never'}")
        
        # Save the actual token
        service_token = token_data['token']
        print(f"   Token: {service_token[:20]}...{service_token[-10:]}")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to create service account token: {e}")
        return
    
    # Step 3: Test the service account token
    print("\n3️⃣ Testing service account token authentication...")
    token_client = SkyPilotAPIClient(endpoint, service_token)
    
    try:
        # Remove basic auth and use only the bearer token
        token_client.session.auth = None
        
        # Test health check with token
        health = token_client.health_check()
        print(f"✅ Successfully authenticated with service account token")
        print(f"   Authenticated user: {health.get('user', {}).get('name', 'unknown')}")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to authenticate with service account token: {e}")
        return
    
    # Step 4: List tokens
    print("\n4️⃣ Listing service account tokens...")
    try:
        tokens = token_client.list_service_account_tokens()
        print(f"✅ Found {len(tokens)} service account token(s):")
        for token in tokens:
            print(f"   - {token['token_name']} (created: {time.ctime(token['created_at'])})")
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to list service account tokens: {e}")
    
    # Step 5: Test API functionality
    print("\n5️⃣ Testing API functionality with service account token...")
    try:
        # This will create a request task, so we just check the response
        response = token_client.session.post(
            token_client._get_api_url('/api/v1/status'),
            json={}
        )
        if response.status_code == 200:
            print("✅ Successfully made API call with service account token")
        else:
            print(f"⚠️ API call returned status {response.status_code}")
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to make API call: {e}")
    
    # Step 6: Cleanup (optional)
    print("\n6️⃣ Cleaning up...")
    try:
        # Delete the token we created
        result = token_client.delete_service_account_token(token_data['token_id'])
        print(f"✅ Deleted service account token: {result.get('message', 'Success')}")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to delete service account token: {e}")
    
    print("\n🎉 Demo completed successfully!")
    print("\nNext steps:")
    print("• Store your service account token securely (environment variable, secret manager)")
    print("• Use the token in your CI/CD pipelines and automation scripts")
    print("• Monitor token usage through the dashboard")
    print("• Rotate tokens regularly for security")


def demo_api_usage(endpoint: str, token: str):
    """Demonstrate API usage with an existing service account token."""
    print("🔧 Service Account Token API Usage Demo")
    print("=" * 50)
    
    client = SkyPilotAPIClient(endpoint, token)
    
    # Test health check
    try:
        health = client.health_check()
        print(f"✅ Health check successful")
        print(f"   User: {health.get('user', {}).get('name', 'unknown')}")
    except Exception as e:
        print(f"❌ Health check failed: {e}")
        return
    
    # Example: List tokens
    try:
        tokens = client.list_service_account_tokens()
        print(f"\n📋 You have {len(tokens)} service account token(s)")
        for token_info in tokens:
            last_used = token_info.get('last_used_at')
            last_used_str = time.ctime(last_used) if last_used else "Never"
            print(f"   • {token_info['token_name']} (last used: {last_used_str})")
    except Exception as e:
        print(f"❌ Failed to list tokens: {e}")


def main():
    parser = argparse.ArgumentParser(description="SkyPilot Service Account Token Demo")
    parser.add_argument('--endpoint', required=True, help='SkyPilot API server endpoint')
    parser.add_argument('--username', help='Username for basic auth (for initial setup)')
    parser.add_argument('--password', help='Password for basic auth (for initial setup)')
    parser.add_argument('--token', help='Existing service account token to test')
    parser.add_argument('--mode', choices=['setup', 'test'], default='setup',
                       help='Demo mode: setup (create token) or test (use existing token)')
    
    args = parser.parse_args()
    
    if args.mode == 'setup':
        if not args.username or not args.password:
            print("❌ Username and password required for setup mode")
            print("Usage: python service_account_demo.py --endpoint URL --username USER --password PASS")
            sys.exit(1)
        demo_basic_flow(args.endpoint, args.username, args.password)
    else:
        if not args.token:
            print("❌ Token required for test mode")
            print("Usage: python service_account_demo.py --endpoint URL --token TOKEN --mode test")
            sys.exit(1)
        demo_api_usage(args.endpoint, args.token)


if __name__ == '__main__':
    main() 
