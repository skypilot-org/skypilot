#!/usr/bin/env python3
"""
Automated Okta OAuth2 Login Script for SkyPilot Testing

This script automates the OAuth2 login flow for test users with MFA disabled.
It simulates the browser-based OAuth flow to obtain authentication cookies
that can be used with SkyPilot CLI.

Usage:
    python3 okta_auto_login.py <endpoint> <username> <password> <client_id>

Example:
    python3 okta_auto_login.py http://localhost:30082 test-user@example.com password123 client_id_here
"""

import base64
import json
import logging
import re
import sys
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


class OktaAutoLogin:
    """Handles automated Okta OAuth2 login flow"""

    def __init__(self, endpoint: str, username: str, password: str,
                 client_id: str):
        self.endpoint = endpoint.rstrip('/')
        self.username = username
        self.password = password
        self.client_id = client_id
        self.session = requests.Session()

        # Set user agent to avoid bot detection
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })

    def get_auth_cookies(self) -> Optional[Dict[str, str]]:
        """
        Perform the complete OAuth2 flow to get authentication cookies

        Returns:
            Dictionary of authentication cookies if successful, None otherwise
        """
        try:
            # Step 1: Initiate OAuth flow
            logger.info("Step 1: Initiating OAuth flow...")
            okta_auth_url = self._start_oauth_flow()
            if not okta_auth_url:
                return None

            # Step 2: Get login form from Okta
            logger.info("Step 2: Getting Okta login form...")
            form_data = self._get_login_form(okta_auth_url)
            if not form_data:
                return None

            # Step 3: Submit credentials
            logger.info("Step 3: Submitting credentials...")
            if not self._submit_credentials(form_data):
                return None

            # Step 4: Complete OAuth flow and extract cookies
            logger.info("Step 4: Completing OAuth flow...")
            auth_cookies = self._extract_auth_cookies()

            if auth_cookies:
                logger.info("✅ Successfully obtained authentication cookies")
                return auth_cookies
            else:
                logger.error("❌ No authentication cookies found")
                return None

        except Exception as e:
            logger.error(f"❌ Authentication failed: {str(e)}")
            return None

    def _start_oauth_flow(self) -> Optional[str]:
        """Start the OAuth flow and get Okta authorization URL"""
        try:
            auth_url = f"{self.endpoint}/oauth2/start"
            response = self.session.get(auth_url,
                                        allow_redirects=False,
                                        timeout=30)

            if response.status_code == 302:
                okta_auth_url = response.headers.get('Location')
                if okta_auth_url and 'okta.com' in okta_auth_url:
                    logger.info(f"Got Okta auth URL: {okta_auth_url[:100]}...")
                    return okta_auth_url
                else:
                    logger.error("Invalid redirect URL from OAuth start")
                    return None
            else:
                logger.error(
                    f"Unexpected response from OAuth start: {response.status_code}"
                )
                return None

        except requests.RequestException as e:
            logger.error(f"Network error during OAuth start: {e}")
            return None

    def _get_login_form(self, okta_auth_url: str) -> Optional[Dict[str, str]]:
        """Get the Okta login form and extract necessary parameters"""
        try:
            response = self.session.get(okta_auth_url, timeout=30)
            if response.status_code != 200:
                logger.error(
                    f"Could not access Okta login page: {response.status_code}")
                return None

            html = response.text

            # Extract form action URL
            action_match = re.search(r'<form[^>]+action="([^"]*)"[^>]*>', html,
                                     re.IGNORECASE)
            if action_match:
                action_url = action_match.group(1)
                if action_url.startswith('/'):
                    parsed = urlparse(okta_auth_url)
                    action_url = f"{parsed.scheme}://{parsed.netloc}{action_url}"
            else:
                # Fallback: construct URL from current page
                parsed = urlparse(okta_auth_url)
                action_url = f"{parsed.scheme}://{parsed.netloc}/api/v1/authn"

            # Extract hidden form fields
            form_data = {'username': self.username, 'password': self.password}

            # Extract common hidden fields
            hidden_fields = [
                'state', 'nonce', '_xsrfToken', 'fromURI', 'okta-signin-submit',
                'relayState', 'sessionToken', 'SAMLRequest'
            ]

            for field in hidden_fields:
                match = re.search(rf'name="{field}"[^>]*value="([^"]*)"', html,
                                  re.IGNORECASE)
                if match:
                    form_data[field] = match.group(1)

            form_data['action_url'] = action_url
            logger.info(f"Extracted form data with {len(form_data)} fields")
            return form_data

        except requests.RequestException as e:
            logger.error(f"Network error getting login form: {e}")
            return None

    def _submit_credentials(self, form_data: Dict[str, str]) -> bool:
        """Submit login credentials to Okta"""
        try:
            action_url = form_data.pop('action_url')

            # Add form headers
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate'
            }

            response = self.session.post(action_url,
                                         data=form_data,
                                         headers=headers,
                                         allow_redirects=True,
                                         timeout=30)

            # Check if login was successful
            if response.status_code == 200:
                # Look for error messages in the response
                if 'error' in response.text.lower(
                ) or 'invalid' in response.text.lower():
                    if 'password' in response.text.lower():
                        logger.error("❌ Invalid username or password")
                    else:
                        logger.error("❌ Login failed - check credentials")
                    return False
                else:
                    logger.info("✅ Credentials submitted successfully")
                    return True
            else:
                logger.error(
                    f"Login submission failed with status: {response.status_code}"
                )
                return False

        except requests.RequestException as e:
            logger.error(f"Network error submitting credentials: {e}")
            return False

    def _extract_auth_cookies(self) -> Optional[Dict[str, str]]:
        """Extract authentication cookies from the session"""
        try:
            # Try to access the callback to ensure we have proper cookies
            callback_url = f"{self.endpoint}/oauth2/callback"
            response = self.session.get(callback_url,
                                        allow_redirects=True,
                                        timeout=30)

            # Extract OAuth2 proxy cookies
            auth_cookies = {}
            cookie_names = [
                '_oauth2_proxy', '_oauth2_proxy_csrf', 'oauth2_proxy_1',
                '_oauth2_proxy_session', 'oauth2-proxy', 'session'
            ]

            for cookie in self.session.cookies:
                if any(name in cookie.name.lower() for name in cookie_names):
                    auth_cookies[cookie.name] = cookie.value
                    logger.info(f"Found auth cookie: {cookie.name}")

            return auth_cookies if auth_cookies else None

        except requests.RequestException as e:
            logger.error(f"Network error extracting cookies: {e}")
            return None

    def create_token(self, cookies: Dict[str, str]) -> str:
        """Create a base64-encoded token from authentication cookies"""
        token_data = {'v': 1, 'user': self.username, 'cookies': cookies}
        token_json = json.dumps(token_data)
        token_bytes = token_json.encode('utf-8')
        return base64.b64encode(token_bytes).decode('utf-8')


def main():
    """Main function to handle command line arguments and perform login"""
    if len(sys.argv) != 5:
        print(
            "Usage: python3 okta_auto_login.py <endpoint> <username> <password> <client_id>"
        )
        print(
            "Example: python3 okta_auto_login.py http://localhost:30082 test@example.com password123 client_id"
        )
        sys.exit(1)

    endpoint, username, password, client_id = sys.argv[1:5]

    # Validate inputs
    if not all([endpoint, username, password, client_id]):
        logger.error("All parameters are required")
        sys.exit(1)

    if not endpoint.startswith(('http://', 'https://')):
        logger.error("Endpoint must start with http:// or https://")
        sys.exit(1)

    logger.info(f"Starting automated login for user: {username}")
    logger.info(f"Endpoint: {endpoint}")

    # Perform login
    login_handler = OktaAutoLogin(endpoint, username, password, client_id)
    cookies = login_handler.get_auth_cookies()

    if cookies:
        token = login_handler.create_token(cookies)
        print(f"SUCCESS:{token}")
        logger.info("🎉 Login completed successfully!")
    else:
        print("FAILED:Could not authenticate")
        logger.error("❌ Login failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
