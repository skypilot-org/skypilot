"""Basic example demonstrating sandbox file operations."""

import os
from dotenv import load_dotenv
from agent_sandbox import Sandbox

# Load environment variables from .env file
load_dotenv()


def main():
    """Example of basic file operations in sandbox."""
    sandbox_url = os.getenv("SANDBOX_BASE_URL", "http://localhost:8080")
    client = Sandbox(base_url=sandbox_url)

    session = client.jupyter.create_session(
        kernel_name="python3",
    )
    client.jupyter.execute_code(
        code="foo=1",
        kernel_name="python3",
        session_id=session.data.session_id
    )
    result = client.jupyter.execute_code(
        code="print(foo)",
        session_id=session.data.session_id,
        kernel_name="python3",
    )
    print("Code execution result:", result.data.outputs[0].text)
    client.shell.exec_command(command="pip3.12 install agent-sandbox")

    result = client.jupyter.execute_code(
        code="""from agent_sandbox import Sandbox
sandbox = Sandbox(base_url="http://localhost:8080")
context = sandbox.sandbox.get_context()
print(context)
""",
        kernel_name="python3.12",
    )
    print("After installed code result:", result.data.outputs[0].text)


if __name__ == "__main__":
    main()
