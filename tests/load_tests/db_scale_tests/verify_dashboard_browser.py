#!/usr/bin/env python3
"""
Browser verification script for dashboard pages.
Verifies that clusters and jobs are loaded in the dashboard.

Usage:
    python3 verify_dashboard_browser.py [--endpoint <endpoint>] [--no-headless]

Example:
    python3 verify_dashboard_browser.py --endpoint http://localhost:46580
    python3 verify_dashboard_browser.py --endpoint http://localhost:46580 --no-headless
"""

import argparse
import logging
import re
import sys
import time

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def get_chrome_driver(headless: bool = True) -> webdriver.Chrome:
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
                     wait_time: int = 10) -> None:
    """
    Verify that the jobs page loads and shows job IDs starting with "12xxx".

    Args:
        driver: The Selenium WebDriver instance
        url: URL to navigate to
        wait_time: Maximum time to wait for page to load (default: 10s, matching sky jobs queue timeout)

    Raises:
        TimeoutException: If job ID starting with "12" is not found within wait_time
    """
    logger.info(f"Navigating to jobs page: {url}")
    driver.get(url)

    logger.info(
        f"Waiting for job ID starting with '12' to appear (timeout: {wait_time}s)..."
    )

    # Wait for a table row containing a job ID link starting with "12"
    def find_job_id(d):
        # First check if job ID starting with "12" exists in page source (fast check)
        page_source = d.page_source
        # Look for pattern like href="/dashboard/jobs/12xxx" or >12xxx<
        if not re.search(r'(?:href=["\']/dashboard/jobs/|>)(12\d{2,})',
                         page_source):
            return False

        # Now find the specific row with the job ID
        rows = d.find_elements(By.CSS_SELECTOR, "tbody tr")
        logger.info(
            f"Checking {len(rows)} table rows for job IDs starting with '12'..."
        )
        for row_idx in range(1, len(rows) + 1):
            try:
                # Re-query rows each time to avoid stale element references
                current_rows = d.find_elements(By.CSS_SELECTOR, "tbody tr")
                if row_idx > len(current_rows):
                    continue
                row = current_rows[row_idx - 1]
                # Get links immediately to avoid stale reference
                links = row.find_elements(By.TAG_NAME, "a")
                for link in links:
                    try:
                        link_text = link.text.strip()
                        if link_text.isdigit() and link_text.startswith("12"):
                            logger.info(
                                f"Found job ID starting with '12': {link_text} in row {row_idx}"
                            )
                            return True
                    except Exception:
                        # Stale element, continue to next link
                        continue
            except Exception:
                # Stale element or index out of range, continue to next row
                continue
        return False

    WebDriverWait(driver, wait_time).until(find_job_id)
    logger.info("✅ Jobs page verified successfully!")


def verify_clusters_page(driver: webdriver.Chrome,
                         url: str,
                         wait_time: int = 20) -> None:
    """
    Verify that the clusters page loads and shows clusters with RUNNING status.

    Args:
        driver: The Selenium WebDriver instance
        url: URL to navigate to
        wait_time: Maximum time to wait for page to load (default: 20s, matching sky status timeout)

    Raises:
        TimeoutException: If RUNNING status is not found within wait_time
    """
    logger.info(f"Navigating to clusters page: {url}")
    driver.get(url)

    logger.info(
        f"Waiting for RUNNING status to appear (timeout: {wait_time}s)...")

    # Wait for a table row containing RUNNING status
    def find_running_status(d):
        # First check if RUNNING exists in page source (fast check)
        page_source = d.page_source.upper()
        if "RUNNING" not in page_source:
            return False

        # Now find the specific row(s) with RUNNING status
        rows = d.find_elements(By.CSS_SELECTOR, "tbody tr")
        logger.info(f"Checking {len(rows)} table rows for RUNNING status...")
        for row_idx in range(1, len(rows) + 1):
            try:
                # Re-query rows each time to avoid stale element references
                current_rows = d.find_elements(By.CSS_SELECTOR, "tbody tr")
                if row_idx > len(current_rows):
                    continue
                row = current_rows[row_idx - 1]
                # Get text immediately to avoid stale reference
                row_text = row.text.upper()
                if "RUNNING" in row_text:
                    # Try to extract cluster name from the row (do this immediately)
                    try:
                        cluster_links = row.find_elements(
                            By.CSS_SELECTOR, "a[href*='/dashboard/clusters/']")
                        cluster_name = cluster_links[0].text.strip(
                        ) if cluster_links else "unknown"
                        logger.info(
                            f"Found RUNNING status in row {row_idx} (cluster: {cluster_name})"
                        )
                    except Exception:
                        # If we can't get cluster name, still log the row
                        logger.info(f"Found RUNNING status in row {row_idx}")
                    return True
            except Exception:
                # Stale element or index out of range, continue to next row
                continue
        return False

    WebDriverWait(driver, wait_time).until(find_running_status)
    logger.info("✅ Clusters page verified successfully!")


def main():
    parser = argparse.ArgumentParser(
        description='Verify dashboard pages in browser')
    parser.add_argument(
        '--endpoint',
        '-e',
        default='http://localhost:46580',
        help='SkyPilot API server endpoint (default: http://localhost:46580)')
    parser.add_argument(
        '--no-headless',
        action='store_true',
        help='Run browser in non-headless mode (default: headless)')

    args = parser.parse_args()

    endpoint = args.endpoint.rstrip('/')
    clusters_url = f"{endpoint}/dashboard/clusters"
    jobs_url = f"{endpoint}/dashboard/jobs"

    driver = None
    try:
        driver = get_chrome_driver(headless=not args.no_headless)

        # Verify clusters page (20s timeout, matching sky status timeout)
        verify_clusters_page(driver, clusters_url, wait_time=20)

        # Wait a bit between page loads
        time.sleep(2)

        # Verify jobs page (10s timeout, matching sky jobs queue timeout)
        verify_jobs_page(driver, jobs_url, wait_time=10)

        logger.info("✅ All dashboard pages verified successfully!")
        return 0

    except TimeoutException as e:
        logger.error(f"❌ Verification failed: {str(e)}")
        if driver:
            logger.error("Dumping current HTML content:")
            print("=" * 80)
            print(driver.page_source)
            print("=" * 80)
        return 1
    except Exception as e:
        logger.error(f"❌ Error during verification: {str(e)}")
        if driver:
            logger.error("Dumping current HTML content:")
            print("=" * 80)
            print(driver.page_source)
            print("=" * 80)
        return 1
    finally:
        if driver:
            driver.quit()


if __name__ == "__main__":
    sys.exit(main())
