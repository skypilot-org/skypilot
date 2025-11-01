from setuptools import setup, find_packages

# Read the contents of README file
from pathlib import Path
this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

setup(
    name="agent-sandbox",
    description="Python SDK for the All-in-One Sandbox API",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Agent Infra Team",
    author_email="team@agent-infra.com",
    packages=find_packages(),
    package_data={
        "agent_sandbox": ["py.typed"],
    },
    install_requires=[
        "httpx>=0.23.0,<1",
        "pydantic>=1.9.0,<3",
        "typing_extensions>=4.0.0; python_version < '3.10'",
    ],
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    zip_safe=False,
)