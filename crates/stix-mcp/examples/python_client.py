#!/usr/bin/env python3
"""
Python client example for STIX MCP Server
"""

import requests
import json
from typing import Optional, Dict, Any, List

class McpClient:
    """Python client for STIX MCP Server"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.token: Optional[str] = None
        self.agent_id: Optional[str] = None
        
    def register(self, name: str, permissions: List[str]) -> str:
        """Register as a new agent"""
        url = f"{self.base_url}/agents/register"
        data = {
            "name": name,
            "permissions": permissions
        }
        
        response = requests.post(url, json=data)
        response.raise_for_status()
        
        result = response.json()
        self.token = result["token"]
        self.agent_id = result["agent_id"]
        
        return self.agent_id
    
    def _headers(self) -> Dict[str, str]:
        """Get authorization headers"""
        if not self.token:
            raise ValueError("Not authenticated. Call register() first.")
        return {"Authorization": f"Bearer {self.token}"}
    
    def execute_tool(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool"""
        url = f"{self.base_url}/tools/execute"
        data = {
            "tool_name": tool_name,
            "parameters": parameters
        }
        
        response = requests.post(url, json=data, headers=self._headers())
        response.raise_for_status()
        
        return response.json()
    
    def lock_file(self, file_path: str, duration_secs: Optional[int] = None) -> Dict[str, Any]:
        """Lock a file"""
        params = {"file_path": file_path}
        if duration_secs is not None:
            params["duration_secs"] = duration_secs
        return self.execute_tool("lock_file", params)
    
    def unlock_file(self, file_path: str) -> Dict[str, Any]:
        """Unlock a file"""
        return self.execute_tool("unlock_file", {"file_path": file_path})
    
    def update_progress(self, section: str, content: str, append: bool = True) -> Dict[str, Any]:
        """Update progress"""
        return self.execute_tool("update_progress", {
            "section": section,
            "content": content,
            "append": append
        })
    
    def issue_task(
        self,
        title: str,
        description: str,
        priority: Optional[str] = None,
        assigned_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a task"""
        params = {
            "title": title,
            "description": description
        }
        if priority:
            params["priority"] = priority
        if assigned_to:
            params["assigned_to"] = assigned_to
        return self.execute_tool("issue_task", params)
    
    def search_codebase(
        self,
        query: str,
        file_pattern: Optional[str] = None,
        case_sensitive: bool = False
    ) -> Dict[str, Any]:
        """Search codebase"""
        params = {
            "query": query,
            "case_sensitive": case_sensitive
        }
        if file_pattern:
            params["file_pattern"] = file_pattern
        return self.execute_tool("search_codebase", params)
    
    def list_locks(self) -> Dict[str, Any]:
        """List all locks"""
        return self.execute_tool("list_locks", {})
    
    def get_my_logs(self) -> List[Dict[str, Any]]:
        """Get my logs"""
        url = f"{self.base_url}/logs/my"
        response = requests.get(url, headers=self._headers())
        response.raise_for_status()
        return response.json()


def main():
    """Example usage"""
    client = McpClient("http://localhost:8080")
    
    # Register agent
    print("=== Registering Python Agent ===")
    agent_id = client.register("PythonAgent", [
        "LockFiles",
        "UpdateProgress",
        "SearchCodebase",
        "CreateTasks"
    ])
    print(f"Registered with ID: {agent_id}")
    
    # Lock a file
    print("\n=== Locking file ===")
    result = client.lock_file("src/main.py", duration_secs=300)
    if result["success"]:
        print("✓ File locked successfully")
    else:
        print(f"✗ Failed to lock: {result.get('error')}")
    
    # Search codebase
    print("\n=== Searching codebase ===")
    result = client.search_codebase("def main", file_pattern="*.py")
    if result["success"]:
        matches = result.get("result", {}).get("matches", [])
        print(f"✓ Found {len(matches)} matches")
        for match in matches[:5]:  # Show first 5
            print(f"  - {match}")
    
    # Create task
    print("\n=== Creating task ===")
    result = client.issue_task(
        "Implement Python integration",
        "Add Python client examples and documentation",
        priority="medium",
        assigned_to="PythonAgent"
    )
    if result["success"]:
        print("✓ Task created successfully")
    
    # Update progress
    print("\n=== Updating progress ===")
    result = client.update_progress(
        "Python Integration",
        "Created Python client library and example scripts",
        append=True
    )
    if result["success"]:
        print("✓ Progress updated")
    
    # Unlock file
    print("\n=== Unlocking file ===")
    result = client.unlock_file("src/main.py")
    if result["success"]:
        print("✓ File unlocked")
    
    # View logs
    print("\n=== Viewing logs ===")
    logs = client.get_my_logs()
    print(f"Total log entries: {len(logs)}")
    
    print("\n=== Example completed successfully! ===")


if __name__ == "__main__":
    main()
