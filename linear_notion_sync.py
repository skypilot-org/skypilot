#!/usr/bin/env python3
"""
Script to sync Linear issues to Notion with proposed fixes for skypilot codebase.
"""

import os
import json
import requests
from datetime import datetime
from typing import List, Dict, Optional
import subprocess
import re
from pathlib import Path

# Try to load from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# API Configuration
LINEAR_API_KEY = os.getenv("LINEAR_API_KEY", "")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")


class LinearClient:
    """Client for interacting with Linear API."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.api_url = "https://api.linear.app/graphql"
        self.headers = {
            "Authorization": api_key,
            "Content-Type": "application/json"
        }
    
    def get_assigned_todo_issues(self) -> List[Dict]:
        """Fetch all issues assigned to the current user in Todo state."""
        query = """
        query {
            viewer {
                assignedIssues(filter: { state: { name: { eq: "Todo" } } }) {
                    nodes {
                        id
                        identifier
                        title
                        description
                        url
                        state {
                            name
                        }
                        priority
                        labels {
                            nodes {
                                name
                            }
                        }
                        comments {
                            nodes {
                                body
                                user {
                                    name
                                }
                                createdAt
                            }
                        }
                        createdAt
                        updatedAt
                        team {
                            name
                        }
                        project {
                            name
                        }
                    }
                }
            }
        }
        """
        
        response = requests.post(
            self.api_url,
            headers=self.headers,
            json={"query": query}
        )
        
        if response.status_code != 200:
            raise Exception(f"Linear API error: {response.status_code} - {response.text}")
        
        data = response.json()
        if "errors" in data:
            raise Exception(f"Linear API errors: {data['errors']}")
        
        return data["data"]["viewer"]["assignedIssues"]["nodes"]


class NotionClient:
    """Client for interacting with Notion API."""
    
    def __init__(self, api_key: str, database_id: str):
        self.api_key = api_key
        self.database_id = database_id
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
    
    def create_page(self, issue: Dict, proposed_fix: str) -> Dict:
        """Create a new page in Notion for a Linear issue."""
        
        # Format labels
        labels = [label["name"] for label in issue.get("labels", {}).get("nodes", [])]
        
        # Format comments
        comments_text = ""
        for comment in issue.get("comments", {}).get("nodes", []):
            comments_text += f"**{comment['user']['name']}** ({comment['createdAt']})\n{comment['body']}\n\n"
        
        page_data = {
            "parent": {"database_id": self.database_id},
            "properties": {
                "Name": {
                    "title": [
                        {
                            "text": {
                                "content": f"{issue['identifier']}: {issue['title']}"
                            }
                        }
                    ]
                },
                "Status": {
                    "select": {
                        "name": "Todo"
                    }
                },
                "Linear URL": {
                    "url": issue["url"]
                },
                "Priority": {
                    "select": {
                        "name": issue.get("priority", "None") or "None"
                    }
                },
                "Team": {
                    "rich_text": [
                        {
                            "text": {
                                "content": issue.get("team", {}).get("name", "")
                            }
                        }
                    ]
                }
            },
            "children": [
                {
                    "object": "block",
                    "type": "heading_1",
                    "heading_1": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": issue["title"]
                                }
                            }
                        ]
                    }
                },
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": "Issue Details"
                                }
                            }
                        ]
                    }
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": f"Linear Issue: {issue['identifier']}\n"
                                }
                            }
                        ]
                    }
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": f"Team: {issue.get('team', {}).get('name', 'N/A')}\n"
                                }
                            }
                        ]
                    }
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": f"Labels: {', '.join(labels) if labels else 'None'}"
                                }
                            }
                        ]
                    }
                },
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": "Description"
                                }
                            }
                        ]
                    }
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": issue.get("description", "No description provided.")[:2000]
                                }
                            }
                        ]
                    }
                }
            ]
        }
        
        # Add comments section if there are any
        if comments_text:
            page_data["children"].extend([
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": "Comments"
                                }
                            }
                        ]
                    }
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": comments_text[:2000]
                                }
                            }
                        ]
                    }
                }
            ])
        
        # Add proposed fix section
        page_data["children"].extend([
            {
                "object": "block",
                "type": "divider",
                "divider": {}
            },
            {
                "object": "block",
                "type": "heading_1",
                "heading_1": {
                    "rich_text": [
                        {
                            "text": {
                                "content": "Proposed Fix"
                            }
                        }
                    ]
                }
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "text": {
                                "content": proposed_fix[:2000]
                            }
                        }
                    }
                }
            }
        ])
        
        response = requests.post(
            "https://api.notion.com/v1/pages",
            headers=self.headers,
            json=page_data
        )
        
        if response.status_code not in [200, 201]:
            raise Exception(f"Notion API error: {response.status_code} - {response.text}")
        
        return response.json()


class SkypilotAnalyzer:
    """Analyzer for skypilot codebase to propose fixes."""
    
    def __init__(self, repo_path: str = "/workspace"):
        self.repo_path = Path(repo_path)
    
    def analyze_issue(self, issue: Dict) -> str:
        """Analyze an issue and propose a fix based on the codebase."""
        title = issue["title"]
        description = issue.get("description", "")
        
        # Extract key terms from the issue
        keywords = self._extract_keywords(title + " " + description)
        
        # Search for relevant files
        relevant_files = self._find_relevant_files(keywords)
        
        # Analyze files and generate proposed fix
        proposed_fix = self._generate_proposed_fix(issue, relevant_files)
        
        return proposed_fix
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Extract relevant keywords from issue text."""
        # Remove common words and extract technical terms
        stop_words = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "is", "was", "are", "were"}
        words = re.findall(r'\b\w+\b', text.lower())
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        
        # Add common technical patterns
        technical_patterns = re.findall(r'[A-Z][a-z]+(?:[A-Z][a-z]+)*', text)  # CamelCase
        technical_patterns += re.findall(r'[a-z]+_[a-z]+(?:_[a-z]+)*', text)  # snake_case
        technical_patterns += re.findall(r'[A-Z_]+', text)  # CONSTANTS
        
        return list(set(keywords + [p.lower() for p in technical_patterns]))[:10]
    
    def _find_relevant_files(self, keywords: List[str]) -> List[Dict]:
        """Find files relevant to the keywords."""
        relevant_files = []
        
        # Search for files containing keywords
        for keyword in keywords[:5]:  # Limit to top 5 keywords
            try:
                # Use grep to find files
                cmd = f"grep -r -l --include='*.py' '{keyword}' {self.repo_path}/sky | head -10"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                
                if result.returncode == 0 and result.stdout:
                    files = result.stdout.strip().split('\n')
                    for file_path in files:
                        if file_path and Path(file_path).exists():
                            # Get context around the keyword
                            context_cmd = f"grep -C 3 '{keyword}' '{file_path}' | head -20"
                            context_result = subprocess.run(context_cmd, shell=True, capture_output=True, text=True)
                            
                            relevant_files.append({
                                "path": file_path,
                                "keyword": keyword,
                                "context": context_result.stdout if context_result.returncode == 0 else ""
                            })
            except Exception as e:
                print(f"Error searching for keyword '{keyword}': {e}")
        
        return relevant_files[:10]  # Limit to 10 most relevant files
    
    def _generate_proposed_fix(self, issue: Dict, relevant_files: List[Dict]) -> str:
        """Generate a proposed fix based on issue and relevant files."""
        proposed_fix = f"## Analysis for {issue['identifier']}: {issue['title']}\n\n"
        
        proposed_fix += "### Issue Summary\n"
        proposed_fix += f"{issue.get('description', 'No description provided.')}\n\n"
        
        proposed_fix += "### Relevant Code Locations\n"
        if relevant_files:
            for rf in relevant_files[:5]:
                proposed_fix += f"\n**File:** `{rf['path']}`\n"
                proposed_fix += f"**Keyword:** {rf['keyword']}\n"
                proposed_fix += f"**Context:**\n```python\n{rf['context']}\n```\n"
        else:
            proposed_fix += "No directly relevant files found based on keywords.\n"
        
        proposed_fix += "\n### Proposed Solution\n"
        
        # Analyze issue type and suggest appropriate fix
        title_lower = issue["title"].lower()
        desc_lower = issue.get("description", "").lower()
        
        if "error" in title_lower or "exception" in title_lower:
            proposed_fix += "This appears to be a bug/error fix. Steps to resolve:\n"
            proposed_fix += "1. Reproduce the error condition\n"
            proposed_fix += "2. Add error handling or fix the root cause\n"
            proposed_fix += "3. Add unit tests to prevent regression\n"
        elif "feature" in title_lower or "add" in title_lower or "implement" in title_lower:
            proposed_fix += "This appears to be a new feature. Implementation approach:\n"
            proposed_fix += "1. Design the API/interface for the new feature\n"
            proposed_fix += "2. Implement the core functionality\n"
            proposed_fix += "3. Add documentation and examples\n"
            proposed_fix += "4. Write comprehensive tests\n"
        elif "performance" in title_lower or "optimize" in title_lower:
            proposed_fix += "This appears to be a performance optimization. Approach:\n"
            proposed_fix += "1. Profile the current implementation\n"
            proposed_fix += "2. Identify bottlenecks\n"
            proposed_fix += "3. Implement optimizations\n"
            proposed_fix += "4. Benchmark improvements\n"
        else:
            proposed_fix += "General approach:\n"
            proposed_fix += "1. Understand the current behavior\n"
            proposed_fix += "2. Identify what needs to change\n"
            proposed_fix += "3. Implement the change with minimal impact\n"
            proposed_fix += "4. Test thoroughly\n"
        
        proposed_fix += "\n### Implementation Details\n"
        
        # Add specific suggestions based on file locations
        if relevant_files:
            proposed_fix += "Based on the code analysis, consider modifying:\n"
            for rf in relevant_files[:3]:
                file_name = Path(rf['path']).name
                proposed_fix += f"- `{rf['path']}`: Check the implementation around '{rf['keyword']}'\n"
        
        proposed_fix += "\n### Testing Strategy\n"
        proposed_fix += "1. Unit tests for the modified components\n"
        proposed_fix += "2. Integration tests for the feature\n"
        proposed_fix += "3. Manual testing of the specific scenario mentioned in the issue\n"
        
        return proposed_fix


def main():
    """Main function to sync Linear issues to Notion."""
    
    # Check for required environment variables
    if not LINEAR_API_KEY:
        print("ERROR: LINEAR_API_KEY environment variable is not set!")
        print("\nTo get your Linear API key:")
        print("1. Go to Linear Settings > API")
        print("2. Create a new Personal API key")
        print("3. Set it as: export LINEAR_API_KEY='your-api-key'")
        return
    
    if not NOTION_API_KEY:
        print("ERROR: NOTION_API_KEY environment variable is not set!")
        print("\nTo get your Notion API key:")
        print("1. Go to https://www.notion.so/my-integrations")
        print("2. Create a new integration")
        print("3. Copy the Internal Integration Token")
        print("4. Set it as: export NOTION_API_KEY='your-api-key'")
        return
    
    if not NOTION_DATABASE_ID:
        print("ERROR: NOTION_DATABASE_ID environment variable is not set!")
        print("\nTo get your Notion database ID:")
        print("1. Open your issues database/folder in Notion")
        print("2. Copy the database ID from the URL")
        print("3. Set it as: export NOTION_DATABASE_ID='your-database-id'")
        print("\nThe database ID is the part after the workspace name and before the ?v=")
        print("Example: https://notion.so/workspace/DATABASE_ID_HERE?v=...")
        return
    
    print("Starting Linear to Notion sync...")
    
    try:
        # Initialize clients
        linear = LinearClient(LINEAR_API_KEY)
        notion = NotionClient(NOTION_API_KEY, NOTION_DATABASE_ID)
        analyzer = SkypilotAnalyzer()
        
        # Fetch Linear issues
        print("\nFetching assigned Todo issues from Linear...")
        issues = linear.get_assigned_todo_issues()
        print(f"Found {len(issues)} issues to process")
        
        # Process each issue
        for i, issue in enumerate(issues):
            print(f"\nProcessing issue {i+1}/{len(issues)}: {issue['identifier']} - {issue['title']}")
            
            try:
                # Analyze the issue
                print("  Analyzing codebase for proposed fix...")
                proposed_fix = analyzer.analyze_issue(issue)
                
                # Create Notion page
                print("  Creating Notion page...")
                notion_page = notion.create_page(issue, proposed_fix)
                print(f"  ✓ Successfully created Notion page: {notion_page['url']}")
                
            except Exception as e:
                print(f"  ✗ Error processing issue {issue['identifier']}: {str(e)}")
                continue
        
        print("\n✅ Sync completed!")
        
    except Exception as e:
        print(f"\n❌ Error during sync: {str(e)}")


if __name__ == "__main__":
    main()