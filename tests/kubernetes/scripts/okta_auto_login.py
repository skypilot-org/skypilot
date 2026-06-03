#!/usr/bin/env python3
"""
Automated Okta OAuth2 Login Script for SkyPilot Testing using Selenium

This script automates the OAuth2 login flow for test users with MFA disabled.
It uses Selenium WebDriver to simulate the browser-based OAuth flow and verify
successful authentication by checking for the SkyPilot dashboard.

Usage:
    # Direct login method (original)
    python3 okta_auto_login.py direct --endpoint <endpoint> --username <username> --password <password>

    # Sky API login method (new)
    python3 okta_auto_login.py sky-api --endpoint <endpoint> --username <username> --password <password>

Example:
    python3 okta_auto_login.py direct --endpoint http://localhost:30082 --username test-user@example.com --password password123
    python3 okta_auto_login.py sky-api --endpoint http://localhost:30082 --username test-user@example.com --password password123
"""

import logging
import sys
import threading
import time
import webbrowser

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

# Import SkyPilot SDK
import sky.client.sdk as sdk

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


class OktaAutoLogin:
    """Handles automated Okta OAuth2 login flow using Selenium"""

    def __init__(self, endpoint: str, username: str, password: str):
        self.endpoint = endpoint.rstrip('/')
        self.username = username
        self.password = password
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

    def fill_okta_credentials(self, driver: webdriver.Chrome) -> bool:
        """
        Fill in Okta username and password fields.

        This is a reusable method that can be called from different login flows.

        Args:
            driver: The Selenium WebDriver instance

        Returns:
            True if credentials were filled successfully, False otherwise
        """
        try:
            # Step 1: Wait for and fill username field
            logger.info("Filling username field...")
            username_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.NAME, "identifier")))
            username_field.clear()
            username_field.send_keys(self.username)
            username_field.send_keys(Keys.RETURN)
            logger.info("✅ Username entered and submitted")

            # Step 2: Wait for and fill password field
            logger.info("Filling password field...")
            password_field = WebDriverWait(driver, 30).until(
                EC.presence_of_element_located(
                    (By.NAME, "credentials.passcode")))
            password_field.clear()
            password_field.send_keys(self.password)
            password_field.send_keys(Keys.RETURN)
            logger.info("✅ Password entered and submitted")

            return True
        except TimeoutException as e:
            logger.error(f"❌ Timeout while filling credentials: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"❌ Error filling credentials: {str(e)}")
            return False

    def perform_login(self) -> bool:
        """
        Perform the complete OAuth2 login flow and verify success (direct method).

        Returns:
            True if login and verification successful, False otherwise
        """
        try:
            self.driver = self.get_chrome_driver(headless=True)
            logger.info("✅ Chrome driver initialized")

            # Step 1: Navigate to the endpoint
            logger.info(f"Step 1: Navigating to endpoint: {self.endpoint}")
            self.driver.get(self.endpoint)

            # Step 2: Fill credentials using reusable method
            if not self.fill_okta_credentials(self.driver):
                return False

            # Step 3: Wait for redirect to dashboard
            logger.info("Step 3: Waiting for redirect to dashboard...")
            WebDriverWait(self.driver,
                          60).until(EC.url_contains("/dashboard/clusters"))
            logger.info("✅ Successfully redirected to dashboard")

            # Step 4: Verify SkyPilot logo is present
            logger.info("Step 4: Verifying SkyPilot logo...")
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

    def _selenium_authorize_flow(self, url: str) -> None:
        """Run the Selenium authorize flow in a background thread.

        For /auth/authorize URLs (polling-based PKCE flow):
        1. Navigate to the authorize URL (redirects to Okta)
        2. Fill Okta credentials
        3. After Okta redirects back, click the Authorize button

        For /token?local_port= URLs (legacy localhost callback flow):
        1. Navigate to the token URL (redirects to Okta)
        2. Fill Okta credentials
        3. JavaScript on the page auto-posts token to localhost callback
        """
        try:
            if not self.driver:
                self.driver = self.get_chrome_driver(headless=True)
                logger.info("✅ Chrome driver initialized")

            logger.info(f"Navigating to URL in Selenium browser: {url}")
            self.driver.get(url)

            if '/auth/authorize' in url:
                # Polling-based PKCE flow: the authorize page requires
                # authentication. Navigating to it triggers an Okta redirect.
                # After Okta login, we're redirected back to the authorize page
                # where we need to click the "Authorize" button.
                logger.info(
                    "Detected authorize URL, filling Okta credentials...")
                if not self.fill_okta_credentials(self.driver):
                    logger.error("❌ Failed to fill Okta credentials")
                    return

                # After Okta login, wait for redirect back to authorize page
                # and click the Authorize button
                logger.info("Waiting for Authorize button on authorize page...")
                authorize_btn = WebDriverWait(self.driver, 60).until(
                    EC.element_to_be_clickable((By.ID, "authorize-btn")))
                logger.info("✅ Authorize button found, clicking...")
                authorize_btn.click()

                # Wait for the "Authorization Complete" page to confirm
                # the authorize POST succeeded. The showMessage() JS function
                # replaces document.body with an h2 containing this text.
                WebDriverWait(self.driver, 30).until(
                    lambda d: 'Authorization Complete' in d.page_source)
                logger.info("✅ Authorization completed successfully")

            elif '/token?local_port=' in url:
                # Legacy localhost callback flow: the token page redirects to
                # Okta, then JavaScript posts the token to localhost.
                logger.info("Detected token URL, filling Okta credentials...")
                if not self.fill_okta_credentials(self.driver):
                    logger.error("❌ Failed to fill Okta credentials")
                    return
                logger.info("✅ Credentials filled, waiting for OAuth callback "
                            "to complete...")
            else:
                logger.info(f"URL opened in Selenium browser (unknown URL)")

        except TimeoutException as e:
            logger.error(f"❌ Timeout during Selenium authorize flow: {str(e)}")
        except Exception as e:
            logger.error(f"❌ Error during Selenium authorize flow: {str(e)}")

    def perform_sky_api_login(self) -> bool:
        """
        Perform login by calling SDK's api_login and intercepting the browser.

        This method:
        1. Patches webbrowser.open to intercept the authorize/token URL
        2. Calls sdk.api_login() which triggers the OAuth flow
        3. Runs the Selenium flow in a background thread (so SDK polling
           can start concurrently for the PKCE flow)
        4. Automates Okta login and clicks the Authorize button
        5. The SDK's polling loop picks up the token

        Returns:
            True if login successful, False otherwise
        """
        intercepted_url = [None]
        selenium_thread = [None]

        # Store original webbrowser.open
        original_webbrowser_open = webbrowser.open

        def intercept_webbrowser_open(url: str) -> bool:
            """Intercept webbrowser.open() and run Selenium flow in background.

            The Selenium flow runs in a background thread so that
            sdk.api_login() can start its polling loop concurrently. The
            polling loop waits for the token that gets created when the
            Authorize button is clicked.
            """
            intercepted_url[0] = url
            logger.info(f"✅ Intercepted URL: {url}")

            # Run Selenium flow in background thread so SDK polling can start
            thread = threading.Thread(target=self._selenium_authorize_flow,
                                      args=(url,),
                                      daemon=True)
            thread.start()
            selenium_thread[0] = thread
            return True

        try:
            sdk.webbrowser.open = intercept_webbrowser_open

            logger.info(
                "Patched webbrowser.open to intercept all browser calls")

            logger.info(f"Calling sdk.api_login(endpoint='{self.endpoint}')...")
            try:
                sdk.api_login(endpoint=self.endpoint, relogin=False)
                logger.info("✅ SDK api_login completed successfully")
                return True
            except Exception as e:
                if intercepted_url[0]:
                    logger.error(
                        f"❌ SDK api_login failed after intercepting URL: "
                        f"{str(e)}")
                else:
                    logger.error(f"❌ SDK api_login failed: {str(e)}")
                return False

        except Exception as e:
            logger.error(f"❌ Error during sky api login: {str(e)}")
            return False
        finally:
            sdk.webbrowser.open = original_webbrowser_open
            logger.debug("Restored original webbrowser.open")

            # Wait for Selenium thread to finish
            if selenium_thread[0]:
                selenium_thread[0].join(timeout=10)

            if self.driver:
                time.sleep(2)
                self.driver.quit()
                logger.info("✅ Chrome driver closed")


def validate_endpoint(ctx, param, value):
    """Validate that the endpoint starts with http:// or https://"""
    if not value.startswith(('http://', 'https://')):
        raise click.BadParameter('Endpoint must start with http:// or https://')
    return value


@click.group()
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
@click.pass_context
def cli(ctx, verbose):
    """Automated Okta OAuth2 Login Script for SkyPilot Testing"""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose


@cli.command('direct')
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
@click.pass_context
def direct_login(ctx, endpoint, username, password):
    """
    Direct login method: Navigates directly to endpoint and automates Okta login.

    This is the original method that directly navigates to the endpoint and
    automates the full OAuth flow using Selenium.

    Example:
        python3 okta_auto_login.py direct --endpoint http://localhost:30082 --username test@example.com --password password123
    """
    logger.info(f"Starting direct login for user: {username}")
    logger.info(f"Endpoint: {endpoint}")

    # Perform login
    login_handler = OktaAutoLogin(endpoint, username, password)
    success = login_handler.perform_login()

    if success:
        print("SUCCESS:Login verification completed")
        logger.info("🎉 Login completed successfully!")
    else:
        print("FAILED:Could not authenticate")
        logger.error("❌ Login failed")
        sys.exit(1)


@cli.command('sky-api')
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
@click.pass_context
def sky_api_login(ctx, endpoint, username, password):
    """
    Sky API login method: Calls 'sky api login' and automates the browser flow.

    This method calls 'sky api login -e <endpoint>' and intercepts the browser
    that gets opened, automating the Okta login process. The normal OAuth callback
    flow completes automatically.

    Example:
        python3 okta_auto_login.py sky-api --endpoint http://localhost:30082 --username test@example.com --password password123
    """
    logger.info(f"Starting sky-api login for user: {username}")
    logger.info(f"Endpoint: {endpoint}")

    # Perform login
    login_handler = OktaAutoLogin(endpoint, username, password)
    success = login_handler.perform_sky_api_login()

    if success:
        print("SUCCESS:Login verification completed")
        logger.info("🎉 Login completed successfully!")
    else:
        print("FAILED:Could not authenticate")
        logger.error("❌ Login failed")
        sys.exit(1)


def main():
    """Main entry point for the CLI"""
    cli()


if __name__ == "__main__":
    main()
