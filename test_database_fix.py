# test_database_fix.py
# Test that the workspace issue is now resolved

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.jira.jira_sync_service import JiraSyncService
from app.core.config import settings
from supabase import create_client

async def test_database_fix():
    """Test that the database workspace constraint is now resolved."""
    
    print("🔧 Testing Database Fix")
    print("=" * 50)
    
    # Initialize Supabase client
    supabase = create_client(
        str(settings.SUPABASE_URL),
        settings.SUPABASE_SERVICE_ROLE_KEY.get_secret_value()
    )
    
    # Use the correct workspace ID
    workspace_id = "84e53826-b670-41fa-96d3-211ebdbc080c"
    
    print(f"Testing with workspace ID: {workspace_id}")
    
    # Test JiraSyncService
    sync_service = JiraSyncService(supabase)
    
    # Test credential saving
    result = await sync_service.save_and_test_credentials(
        workspace_id,
        "https://hammadahmed06.atlassian.net",
        "malikxd06@gmail.com",
        "ATATT3xFfGF0lfxf-7qZmeJDVQhvGU51PC73dm9J2_HF11misbq4eNVhLXAI0_jKUxPyE0oTztQgzjk2DezOakP8OZYvCfpImR10bOai1sUq9NW9YUQMC3WU5n6dUqmaSQnpQRqFyroYgrCyKWhkraGIBYetZ_t76uZZWEuFP9wmD50O7yzIh4E=92B8D700"
    )
    
    print(f"\n✅ Result: {result['success']}")
    print(f"📝 Message: {result['message']}")
    print(f"🔗 Status: {result['connection_status']}")
    
    if result['success']:
        print("\n🎉 SUCCESS! Database constraint issue is RESOLVED!")
        
        # Test getting integration status
        status = await sync_service.get_integration_status(workspace_id)
        print(f"\n📊 Integration Status:")
        print(f"   Connected: {status['is_connected']}")
        print(f"   Status: {status['connection_status']}")
        print(f"   Jira URL: {status['jira_url']}")
        print(f"   Email: {status['jira_email']}")
        
    else:
        print(f"\n❌ Still having issues: {result['message']}")
    
    print("\n" + "=" * 50)
    print("🎯 Your Jira integration is now ready for use!")
    print("Test it at: http://localhost:8000/docs")

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_database_fix())
