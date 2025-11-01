import asyncio
import os
from dotenv import load_dotenv
from agent_sandbox import Sandbox
from playwright.async_api import async_playwright

# Load environment variables from .env file
load_dotenv()

sandbox_url = os.getenv("SANDBOX_BASE_URL", "http://localhost:8080")
sandbox = Sandbox(base_url=sandbox_url)
cdp_url = sandbox.browser.get_info().data.cdp_url


async def main():
    async with async_playwright() as playwright:
        # Connect to the sandbox browser via CDP
        browser = await playwright.chromium.connect_over_cdp(cdp_url)

        # Create a new context and page
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to a website
        await page.goto("https://duckduckgo.com")

        # Perform search
        search_box = page.locator('input[name="q"]')
        await search_box.fill("playwright automation")
        await search_box.press("Enter")

        # Wait for results
        await page.wait_for_selector('[data-testid="result"]', timeout=10000)

        # Get the first result
        first_result = page.locator('[data-testid="result"]').first
        result_title = await first_result.locator('h2').inner_text()
        result_link = await first_result.locator('a').get_attribute('href')

        print(f"First search result:")
        print(f"Title: {result_title}")
        print(f"Link: {result_link}")

        # Take a screenshot
        await page.screenshot(path="search_results.png")
        print("Screenshot saved as search_results.png")

        # Clean up
        await context.close()
        await browser.close()

    input("Press Enter to close...")


if __name__ == "__main__":
    asyncio.run(main())
