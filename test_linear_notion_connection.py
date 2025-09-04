#!/usr/bin/env python3
"""
Test script to verify Linear and Notion API connections.
"""

import os
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

LINEAR_API_KEY = os.getenv("LINEAR_API_KEY", "")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")


def test_linear_connection():
    """Test Linear API connection."""
    print("🔍 Testing Linear API connection...")
    
    if not LINEAR_API_KEY:
        print("❌ LINEAR_API_KEY not set in .env file")
        return False
    
    headers = {
        "Authorization": LINEAR_API_KEY,
        "Content-Type": "application/json"
    }
    
    # Simple query to get current user
    query = """
    query {
        viewer {
            id
            name
            email
        }
    }
    """
    
    try:
        response = requests.post(
            "https://api.linear.app/graphql",
            headers=headers,
            json={"query": query}
        )
        
        if response.status_code == 200:
            data = response.json()
            if "data" in data and "viewer" in data["data"]:
                user = data["data"]["viewer"]
                print(f"✅ Linear API connected! Logged in as: {user.get('name', 'Unknown')} ({user.get('email', 'Unknown')})")
                return True
            else:
                print(f"❌ Linear API error: {data}")
                return False
        else:
            print(f"❌ Linear API HTTP error: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Linear API connection failed: {e}")
        return False


def test_notion_connection():
    """Test Notion API connection."""
    print("\n🔍 Testing Notion API connection...")
    
    if not NOTION_API_KEY:
        print("❌ NOTION_API_KEY not set in .env file")
        return False
    
    if not NOTION_DATABASE_ID:
        print("❌ NOTION_DATABASE_ID not set in .env file")
        return False
    
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28"
    }
    
    try:
        # Test by retrieving database info
        response = requests.get(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}",
            headers=headers
        )
        
        if response.status_code == 200:
            data = response.json()
            title = "Unknown"
            if "title" in data and len(data["title"]) > 0:
                title = data["title"][0].get("plain_text", "Unknown")
            print(f"✅ Notion API connected! Database: {title}")
            return True
        elif response.status_code == 404:
            print("❌ Notion database not found. Please check:")
            print("   1. The database ID is correct")
            print("   2. You've shared the database with your integration")
            return False
        else:
            print(f"❌ Notion API error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ Notion API connection failed: {e}")
        return False


def count_linear_issues():
    """Count Linear issues in Todo state."""
    print("\n📊 Checking Linear issues...")
    
    if not LINEAR_API_KEY:
        return
    
    headers = {
        "Authorization": LINEAR_API_KEY,
        "Content-Type": "application/json"
    }
    
    query = """
    query {
        viewer {
            assignedIssues(filter: { state: { name: { eq: "Todo" } } }) {
                nodes {
                    id
                    identifier
                    title
                }
            }
        }
    }
    """
    
    try:
        response = requests.post(
            "https://api.linear.app/graphql",
            headers=headers,
            json={"query": query}
        )
        
        if response.status_code == 200:
            data = response.json()
            issues = data["data"]["viewer"]["assignedIssues"]["nodes"]
            print(f"✅ Found {len(issues)} issues in Todo state:")
            for issue in issues[:5]:  # Show first 5
                print(f"   - {issue['identifier']}: {issue['title']}")
            if len(issues) > 5:
                print(f"   ... and {len(issues) - 5} more")
    except Exception as e:
        print(f"❌ Failed to count issues: {e}")


def main():
    print("🧪 Testing Linear-Notion Integration Setup\n")
    
    # Check if .env exists
    if not os.path.exists(".env"):
        print("❌ .env file not found!")
        print("   Run: cp .env.example .env")
        print("   Then add your API keys to the .env file")
        return
    
    # Test connections
    linear_ok = test_linear_connection()
    notion_ok = test_notion_connection()
    
    if linear_ok:
        count_linear_issues()
    
    print("\n" + "="*50)
    if linear_ok and notion_ok:
        print("✅ All connections successful! You're ready to run the sync.")
        print("   Run: python3 linear_notion_sync.py")
    else:
        print("❌ Some connections failed. Please check your API keys and configuration.")


if __name__ == "__main__":
    main()