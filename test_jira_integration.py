# test_jira_integration.py
# Simple test script for Jira integration

import os
import sys
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.jira.jira_client import JiraClient
from app.services.encryption.simple_credential_store import simple_credential_store

async def test_jira_integration():
    """Test the Jira integration step by step."""
    
    print("🔧 Testing Jira Integration")
    print("=" * 50)
    
    # Get Jira credentials from user input
    jira_url = input("Enter your Jira URL (e.g., https://yourcompany.atlassian.net): ").strip()
    jira_email = input("Enter your Jira email: ").strip()
    jira_token = input("Enter your Jira API token: ").strip()
    
    if not all([jira_url, jira_email, jira_token]):
        print("❌ All fields are required!")
        return
    
    print("\n1. Testing Jira Connection...")
    print("-" * 30)
    
    # Test Jira client
    try:
        client = JiraClient(jira_url, jira_email, jira_token)
        success, message = client.connect()
        
        if success:
            print(f"✅ Connection successful: {message}")
            
            # Test getting projects
            print("\n2. Fetching available projects...")
            print("-" * 30)
            
            projects = client.get_all_projects()
            if projects:
                print(f"✅ Found {len(projects)} projects:")
                for i, project in enumerate(projects[:5], 1):  # Show first 5
                    print(f"   {i}. {project['key']} - {project['name']}")
                
                if len(projects) > 5:
                    print(f"   ... and {len(projects) - 5} more")
                
                # Test fetching issues from first project
                if projects:
                    test_project = projects[0]
                    print(f"\n3. Testing issue fetch from project '{test_project['key']}'...")
                    print("-" * 30)
                    
                    issues = client.get_project_issues(test_project['key'], max_results=3)
                    if issues:
                        print(f"✅ Found {len(issues)} issues:")
                        for issue in issues:
                            fields = issue.get('fields', {})
                            summary = fields.get('summary', 'No title')
                            status = fields.get('status', {}).get('name', 'No status')
                            print(f"   - {issue.get('key', 'No key')}: {summary} [{status}]")
                    else:
                        print("ℹ️  No issues found in this project")
            else:
                print("ℹ️  No projects found or no access to projects")
        else:
            print(f"❌ Connection failed: {message}")
            return
        
        client.close()
        
    except Exception as e:
        print(f"❌ Error during testing: {str(e)}")
        return
    
    print("\n4. Testing credential encoding/decoding...")
    print("-" * 30)
    
    # Test credential storage
    try:
        encoded = simple_credential_store.encode_credential(jira_token)
        decoded = simple_credential_store.decode_credential(encoded)
        
        if decoded == jira_token:
            print("✅ Credential encoding/decoding works correctly")
        else:
            print("❌ Credential encoding/decoding failed")
    except Exception as e:
        print(f"❌ Credential encoding error: {str(e)}")
    
    print("\n🎉 Jira integration test completed!")
    print("=" * 50)
    print("\nNext steps:")
    print("1. Make sure your database tables are set up correctly")
    print("2. Test the API endpoints using the FastAPI docs at http://localhost:8000/docs")
    print("3. Use the /api/integrations/jira/connect endpoint to save credentials")
    print("4. Use the /api/integrations/jira/sync/{project_id} endpoint to sync issues")

if __name__ == "__main__":
    asyncio.run(test_jira_integration())
