import os
import json
from dotenv import load_dotenv
from openai import OpenAI
from agent_sandbox import Sandbox

# Load environment variables from .env file
load_dotenv()


# set .env
client = OpenAI()
sandbox_url = os.getenv("SANDBOX_BASE_URL", "http://localhost:8080")
sandbox = Sandbox(base_url=sandbox_url)


# define a tool to run code in the sandbox
def run_code(code, lang="python"):
    if lang == "python":
        return sandbox.jupyter.execute_code(code=code).data
    return sandbox.nodejs.execute_code(code=code).data


# Using OpenAI
response = client.chat.completions.create(
    model=os.getenv("OPENAI_MODEL_ID", "gpt-5-2025-08-07"),
    messages=[{"role": "user", "content": "calculate 1+1"}],
    tools=[
        {
            "type": "function",
            "function": {
                "name": "run_code",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "lang": {"type": "string"},
                    },
                },
            },
        }
    ],
)


if response.choices[0].message.tool_calls:
    args = json.loads(
        response.choices[0].message.tool_calls[0].function.arguments)
    print("args", args)
    result = run_code(**args)
    print(result.outputs[0].text)
