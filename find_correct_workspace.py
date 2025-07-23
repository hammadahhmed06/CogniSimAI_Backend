# find_correct_workspace.py
# Find the correct workspace ID from the database

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.config import settings
from supabase import create_client

def find_correct_workspace():
    """Find the correct workspace ID and fix the integration."""
    
    print("🔍 Finding Correct Workspace ID")
    print("=" * 50)
    
    # Initialize Supabase client
    supabase = create_client(
        str(settings.SUPABASE_URL),
        settings.SUPABASE_SERVICE_ROLE_KEY.get_secret_value()
    )
    
    try:
        # Get all workspaces
        workspaces = supabase.table("workspaces").select("id, name, created_at").execute()
        
        print("📋 Available Workspaces:")
        for workspace in workspaces.data:
            print(f"   - ID: {workspace['id']}")
            print(f"     Name: {workspace['name']}")
            print(f"     Created: {workspace['created_at']}")
            print()
        
        # Get all teams
        teams = supabase.table("teams").select("id, workspace_id, name, created_at").execute()
        
        print("👥 Available Teams:")
        for team in teams.data:
            print(f"   - Team ID: {team['id']}")
            print(f"     Workspace ID: {team['workspace_id']}")
            print(f"     Name: {team['name']}")
            print(f"     Created: {team['created_at']}")
            print()
        
        # Find the correct workspace ID for 'CogniSim Corp'
        cognisim_workspace = None
        for workspace in workspaces.data:
            if 'CogniSim' in workspace['name']:
                cognisim_workspace = workspace
                break
        
        if cognisim_workspace:
            print(f"✅ Found CogniSim Workspace:")
            print(f"   ID: {cognisim_workspace['id']}")
            print(f"   Name: {cognisim_workspace['name']}")
            
            # Check if user is a member of any team in this workspace
            user_id = "3b2f780b-2943-495b-a8e9-af973ece2a18"
            user_teams = supabase.table("team_members").select("team_id").eq("user_id", user_id).execute()
            
            if user_teams.data:
                team_id = user_teams.data[0]['team_id']
                # Get the team details
                team_details = supabase.table("teams").select("workspace_id, name").eq("id", team_id).execute()
                if team_details.data:
                    print(f"\n👤 User is member of team: {team_details.data[0]['name']}")
                    print(f"   Team's workspace ID: {team_details.data[0]['workspace_id']}")
                    
                    return {
                        'correct_workspace_id': cognisim_workspace['id'],
                        'user_team_workspace_id': team_details.data[0]['workspace_id'],
                        'workspace_name': cognisim_workspace['name']
                    }
            
            return {
                'correct_workspace_id': cognisim_workspace['id'],
                'workspace_name': cognisim_workspace['name']
            }
        else:
            print("❌ No CogniSim workspace found!")
            return None
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return None

if __name__ == "__main__":
    result = find_correct_workspace()
    if result:
        print("\n🎯 SOLUTION:")
        print("=" * 50)
        print(f"Use this workspace ID: {result['correct_workspace_id']}")
        print("Update your integration routes to use this ID instead.")
    else:
        print("❌ Could not find the correct workspace ID")
