import os
import asyncio
import base64
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from agent_sandbox import Sandbox

# Load environment variables from .env file
load_dotenv()


async def site_to_markdown():
    # initialize sandbox client
    sandbox_url = os.getenv("SANDBOX_BASE_URL", "http://localhost:8080")
    c = Sandbox(base_url=sandbox_url)
    home_dir = c.sandbox.get_context().home_dir

    # Browser: automation to download html
    async with async_playwright() as p:
        browser_info = c.browser.get_info().data
        page = await (await p.chromium.connect_over_cdp(browser_info.cdp_url)).new_page(
            viewport={
                "width": browser_info.viewport.width,
                "height": browser_info.viewport.height,
            }
        )
        await page.goto("https://sandbox.agent-infra.com/", wait_until="networkidle")
        html = await page.content()
        screenshot_b64 = base64.b64encode(
            await page.screenshot(full_page=False, type='png')
        ).decode('utf-8')

    # Jupyter: Run code in sandbox to convert html to markdown
    c.jupyter.execute_code(
        code=f"""
from markdownify import markdownify
html = '''{html}'''
screenshot_b64 = "{screenshot_b64}"

md = f"{{markdownify(html)}}\\n\\n![Screenshot](data:image/png;base64,{{screenshot_b64}})"

with open('{home_dir}/site.md', 'w') as f:
    f.write(md)

print("Done!")
"""
    )

    # BasH: execute command to list files in sandbox
    list_result = c.shell.exec_command(command=f"ls -lh {home_dir}")
    print(f"\nFiles in sandbox home directory:\n{list_result.data.output}")

    open("./output.md", "w").write(
        c.file.read_file(file=f"{home_dir}/site.md").data.content
    )

    return "./output.md"


if __name__ == "__main__":
    # Run the async function
    result = asyncio.run(site_to_markdown())
    print(f"\nMarkdown file saved at: {result}")
