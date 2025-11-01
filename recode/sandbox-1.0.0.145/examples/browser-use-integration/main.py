import asyncio
import os
from dotenv import load_dotenv
from agent_sandbox import Sandbox
from browser_use import Agent, Tools
from browser_use.browser import BrowserProfile, BrowserSession
from browser_use.llm import ChatOpenAI

# Load environment variables from .env file
load_dotenv()

sandbox_url = os.getenv("SANDBOX_BASE_URL", "http://localhost:8080")
sandbox = Sandbox(base_url=sandbox_url)
cdp_url = sandbox.browser.get_info().data.cdp_url

browser_session = BrowserSession(
    browser_profile=BrowserProfile(cdp_url=cdp_url, is_local=True)
)
tools = Tools()


async def main():
    agent = Agent(
        task='Visit https://duckduckgo.com and search for "browser-use founders"',
        llm=ChatOpenAI(model=os.getenv("OPENAI_MODEL_ID", "gpt-5-2025-08-07")),
        tools=tools,
        browser_session=browser_session,
    )

    await agent.run()
    await browser_session.kill()

    input("Press Enter to close...")


if __name__ == "__main__":
    asyncio.run(main())
