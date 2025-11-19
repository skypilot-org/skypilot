#!/usr/bin/env python3
"""
Browser verification script for dashboard pages.
Opens a non-headless browser to verify that clusters and jobs are loaded in the dashboard.

Usage:
    python3 verify_dashboard_browser.py [--endpoint <endpoint>] [--wait-time <seconds>]

Example:
    python3 verify_dashboard_browser.py --endpoint http://localhost:46580 --wait-time 30
"""

import argparse
import logging
import sys
import time

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def get_chrome_driver(headless: bool = False) -> webdriver.Chrome:
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


def verify_jobs_page(driver: webdriver.Chrome,
                     url: str,
                     wait_time: int = 10) -> bool:
    """
    Verify that the jobs page loads and shows job IDs starting with "12xxx".

    Args:
        driver: The Selenium WebDriver instance
        url: URL to navigate to
        wait_time: Maximum time to wait for page to load (default: 10s, matching sky jobs queue timeout)

    Returns:
        True if page loads successfully and shows job IDs starting with "12"
    """
    logger.info(f"Navigating to jobs page: {url}")
    driver.get(url)

    logger.info(
        f"Waiting for job ID starting with '12' to appear (timeout: {wait_time}s)..."
    )
    # Wait for a table row containing a job ID link starting with "12"
    WebDriverWait(driver, wait_time).until(lambda d: any(
        link.text.strip().isdigit() and link.text.strip().startswith(
            "12") for row in d.find_elements(By.CSS_SELECTOR, "tbody tr") for
        link in row.find_elements(By.TAG_NAME, "a") if link.text.strip()))

    logger.info(
        f"✅ Jobs page verified successfully! Found job ID starting with '12'")
    logger.info(f"Page URL: {driver.current_url}")
    return True


def verify_clusters_page(driver: webdriver.Chrome,
                         url: str,
                         wait_time: int = 20) -> bool:
    """
    Verify that the clusters page loads and shows clusters with RUNNING status.

    Args:
        driver: The Selenium WebDriver instance
        url: URL to navigate to
        wait_time: Maximum time to wait for page to load (default: 20s, matching sky status timeout)

    Returns:
        True if page loads successfully and shows RUNNING status
    """
    logger.info(f"Navigating to clusters page: {url}")
    driver.get(url)

    logger.info(
        f"Waiting for RUNNING status to appear (timeout: {wait_time}s)...")
    # Wait for a table row containing RUNNING status
    WebDriverWait(driver,
                  wait_time).until(lambda d: any("RUNNING" in row.text.upper(
                  ) for row in d.find_elements(By.CSS_SELECTOR, "tbody tr")))

    logger.info(f"✅ Clusters page verified successfully! Found RUNNING status")
    logger.info(f"Page URL: {driver.current_url}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Verify dashboard pages in browser')
    parser.add_argument(
        '--endpoint',
        '-e',
        default='http://localhost:46580',
        help='SkyPilot API server endpoint (default: http://localhost:46580)')
    parser.add_argument(
        '--keep-open',
        '-k',
        action='store_true',
        help='Keep browser open after verification (default: False)')

    args = parser.parse_args()

    endpoint = args.endpoint.rstrip('/')
    clusters_url = f"{endpoint}/dashboard/clusters"
    jobs_url = f"{endpoint}/dashboard/jobs"

    logger.info(f"Starting dashboard verification")
    logger.info(f"Endpoint: {endpoint}")
    logger.info(f"Clusters URL: {clusters_url}")
    logger.info(f"Jobs URL: {jobs_url}")
    logger.info(f"Browser will be NON-HEADLESS (visible)")

    driver = None
    try:
        # Open browser in NON-HEADLESS mode so user can see what's happening
        driver = get_chrome_driver(headless=False)
        logger.info("✅ Chrome driver initialized (NON-HEADLESS)")

        # Verify clusters page (20s timeout, matching sky status timeout)
        clusters_ok = verify_clusters_page(driver, clusters_url, wait_time=20)

        # Wait a bit between page loads
        time.sleep(2)

        # Verify jobs page (10s timeout, matching sky jobs queue timeout)
        jobs_ok = verify_jobs_page(driver, jobs_url, wait_time=10)

        if clusters_ok and jobs_ok:
            logger.info("✅ All dashboard pages verified successfully!")

            if args.keep_open:
                logger.info("Browser will remain open for manual inspection...")
                logger.info("Press Enter to close the browser...")
                input()
            else:
                logger.info(
                    "Keeping browser open for 60 seconds for manual inspection..."
                )
                time.sleep(60)

            return 0
        else:
            logger.error("❌ Some dashboard pages failed to load")
            if args.keep_open:
                logger.info("Browser will remain open for debugging...")
                logger.info("Press Enter to close the browser...")
                input()
            return 1

    except Exception as e:
        logger.error(f"❌ Error during verification: {str(e)}")
        if driver and args.keep_open:
            logger.info("Browser will remain open for debugging...")
            logger.info("Press Enter to close the browser...")
            input()
        return 1
    finally:
        if driver:
            driver.quit()
            logger.info("✅ Browser closed")


if __name__ == "__main__":
    sys.exit(main())
