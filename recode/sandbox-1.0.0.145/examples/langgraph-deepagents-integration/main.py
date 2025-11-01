import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from deepagents import create_deep_agent
from langchain.chat_models.base import init_chat_model
import os

# Ensure localhost bypasses proxy
os.environ['NO_PROXY'] = 'localhost,127.0.0.1'


async def main():
    # Collect MCP tools
    mcp_client = MultiServerMCPClient({
        "sandbox": {
            "url": "http://localhost:8080/mcp/",
            "transport": "streamable_http"
        },
    })

    mcp_tools = await mcp_client.get_tools()

    # Create agent
    agent = create_deep_agent(
        tools=mcp_tools,
        system_prompt="You are a deepResearch agent that can use the sandbox to answer questions.",
        model=init_chat_model(
            model=f"openai:{os.getenv('OPENAI_MODEL_ID')}",
            base_url=os.getenv("OPENAI_BASEURL"),
            api_key=os.getenv("OPENAI_API_KEY"),
        ),
    )

    # Stream the agent
    async for chunk in agent.astream(
        {"messages": [{"role": "user", "content": "what is AIO Sandbox?"}]},
        stream_mode="values"
    ):
        if "messages" in chunk:
            chunk["messages"][-1].pretty_print()

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(main())
