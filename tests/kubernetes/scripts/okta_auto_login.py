#!/usr/bin/env python3
"""
Automated Okta OAuth2 Login Script for SkyPilot Testing using Selenium

This script automates the OAuth2 login flow for test users with MFA disabled.
It uses Selenium WebDriver to simulate the browser-based OAuth flow and verify
successful authentication by checking for the SkyPilot dashboard.

Usage:
    python3 okta_auto_login.py --endpoint <endpoint> --username <username> --password <password> --client-id <client_id>

Example:
    python3 okta_auto_login.py --endpoint http://localhost:30082 --username test-user@example.com --password password123 --client-id client_id_here
"""

import logging
import sys

import click
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


class OktaAutoLogin:
    """Handles automated Okta OAuth2 login flow using Selenium"""

    def __init__(self, endpoint: str, username: str, password: str,
                 client_id: str):
        self.endpoint = endpoint.rstrip('/')
        self.username = username
        self.password = password
        self.client_id = client_id
        self.driver = None

    def get_chrome_driver(self, headless: bool) -> webdriver.Chrome:
        """Create and return a Chrome driver with appropriate options."""
        chrome_options = Options()

        if headless:
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-extensions')
            chrome_options.add_argument('--disable-plugins')

        # Additional options for better compatibility
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument(
            '--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )

        # Use ChromeDriverManager to automatically handle driver installation
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=chrome_options)

    def perform_login(self) -> bool:
        """
        Perform the complete OAuth2 login flow and verify success

        Returns:
            True if login and verification successful, False otherwise
        """
        try:
            self.driver = self.get_chrome_driver(headless=True)
            logger.info("✅ Chrome driver initialized")

            # Step 1: Navigate to the endpoint
            logger.info(f"Step 1: Navigating to endpoint: {self.endpoint}")
            self.driver.get(self.endpoint)

            # Step 2: Wait for and fill username field
            logger.info("Step 2: Filling username field...")
            username_field = WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.NAME, "identifier")))
            username_field.clear()
            username_field.send_keys(self.username)
            username_field.send_keys(Keys.RETURN)
            logger.info("✅ Username entered and submitted")

            # Step 3: Wait for and fill password field
            logger.info("Step 3: Filling password field...")
            password_field = WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located(
                    (By.NAME, "credentials.passcode")))
            password_field.clear()
            password_field.send_keys(self.password)
            password_field.send_keys(Keys.RETURN)
            logger.info("✅ Password entered and submitted")

            # Step 4: Wait for redirect to dashboard
            logger.info("Step 4: Waiting for redirect to dashboard...")
            WebDriverWait(self.driver,
                          60).until(EC.url_contains("/dashboard/clusters"))
            logger.info("✅ Successfully redirected to dashboard")

            # Step 5: Verify SkyPilot logo is present
            logger.info("Step 5: Verifying SkyPilot logo...")
            logo_element = WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "img[alt='SkyPilot Logo']")))
            logger.info("✅ SkyPilot logo found on page")

            logger.info("🎉 Login verification completed successfully!")
            return True

        except TimeoutException as e:
            logger.error(f"❌ Timeout during login process: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"❌ Authentication failed: {str(e)}")
            return False
        finally:
            if self.driver:
                self.driver.quit()
                logger.info("✅ Chrome driver closed")


def validate_endpoint(ctx, param, value):
    """Validate that the endpoint starts with http:// or https://"""
    if not value.startswith(('http://', 'https://')):
        raise click.BadParameter('Endpoint must start with http:// or https://')
    return value


@click.command()
@click.option(
    '--endpoint',
    '-e',
    required=True,
    help='SkyPilot API server endpoint (e.g., http://localhost:30082)',
    callback=validate_endpoint)
@click.option('--username',
              '-u',
              required=True,
              help='Okta username/email for authentication')
@click.option('--password',
              '-p',
              required=True,
              help='Okta password for authentication',
              hide_input=True)
@click.option('--client-id', '-c', required=True, help='OAuth2 client ID')
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def main(endpoint, username, password, client_id, verbose):
    """
    Automated Okta OAuth2 Login Script for SkyPilot Testing

    This script automates the OAuth2 login flow for test users with MFA disabled.
    It uses Selenium WebDriver to simulate the browser-based OAuth flow and verify
    successful authentication by checking for the SkyPilot dashboard.

    Example:
        python3 okta_auto_login.py --endpoint http://localhost:30082 --username test@example.com --password password123 --client-id client_id_here
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info(f"Starting automated login for user: {username}")
    logger.info(f"Endpoint: {endpoint}")

    # Perform login
    login_handler = OktaAutoLogin(endpoint, username, password, client_id)
    success = login_handler.perform_login()

    if success:
        print("SUCCESS:Login verification completed")
        logger.info("🎉 Login completed successfully!")
    else:
        print("FAILED:Could not authenticate")
        logger.error("❌ Login failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
