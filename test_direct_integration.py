#!/usr/bin/env python3
"""
Direct Jira Integration Test - bypasses API authentication for testing
Tests the core Jira integration functionality directly.
"""

import asyncio
import sys
from pathlib import Path

# Add the app directory to the path so we can import our modules
sys.path.append(str(Path(__file__).parent / "app"))

from app.services.jira.jira_sync_service import JiraSyncService
from app.core.dependencies import supabase

async def test_direct_integration():
    """Test Jira integration directly without API layer."""
    print("🚀 Testing Direct Jira Integration")
    print("=" * 50)
    
    # Initialize the sync service
    sync_service = JiraSyncService(supabase)
    
    # Test data
    workspace_id = "84e53826-b670-41fa-96d3-211ebdbc080c"
    jira_url = "https://hammadahmed06.atlassian.net"
    jira_email = "malikxd06@gmail.com"
    jira_api_token = "ATATT3xFfGF0yPFHgEFfOXu4s6JlkP8fEP0vdpjvCbJZvUMhVX8k6wKITBwI3aR7cWxRPdXVv1Eg5pGV0HNJ_PrXf4tJ4GiRJ-F9JlpWC2cBxNr4qUNqw-pJ4hP8aWJlOJJhHFd5p9Kj4dFgEt0HXqYvZJBd5Q_DzGM-rNJzD3Q=xX4B2F9A"
    
    try:
        print("🔍 Testing credential save and connection...")
        result = await sync_service.save_and_test_credentials(
            workspace_id=workspace_id,
            jira_url=jira_url,
            jira_email=jira_email,
            jira_api_token=jira_api_token
        )
        
        print(f"✅ Connection test result: {result}")
        
        if result.get('success'):
            print("\n🔍 Testing integration status...")
            status = await sync_service.get_integration_status(workspace_id)
            print(f"✅ Integration status: {status}")
            
            print("\n🔍 Testing credential retrieval...")
            credentials = await sync_service._get_credentials(workspace_id)
            if credentials:
                print(f"✅ Credentials found: URL={credentials['jira_url']}, Email={credentials['jira_email']}")
            else:
                print("❌ No credentials found")
        
    except Exception as e:
        print(f"❌ Test failed: {str(e)}")
        import traceback
        traceback.print_exc()

def main():
    """Run the direct integration test."""
    asyncio.run(test_direct_integration())

if __name__ == "__main__":
    main()
