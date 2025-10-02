"""
Quick test script for email invitation functionality.

Usage:
    python test_email_invitation.py

Requirements:
    - Set RESEND_API_KEY in environment or .env file
    - Install resend: pip install resend
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent))

from app.services.email_service import send_invitation_email


def main():
    """Test email invitation sending."""
    
    # Check if API key is set
    api_key = os.getenv("RESEND_API_KEY") or os.getenv("EMAIL_API_KEY")
    if not api_key:
        print("❌ Error: RESEND_API_KEY or EMAIL_API_KEY not set in environment")
        print("\nTo fix this:")
        print("1. Sign up at https://resend.com")
        print("2. Get your API key from the dashboard")
        print("3. Run: export RESEND_API_KEY=re_xxxxx")
        print("\nOr add to .env file:")
        print("RESEND_API_KEY=re_xxxxx")
        return
    
    # Set defaults if not configured
    if not os.getenv("EMAIL_FROM"):
        os.environ["EMAIL_FROM"] = "onboarding@resend.dev"
        print("ℹ️  Using default sender: onboarding@resend.dev (Resend testing domain)")
    
    if not os.getenv("EMAIL_PROVIDER"):
        os.environ["EMAIL_PROVIDER"] = "resend"
    
    # Get test email from user
    test_email = input("\n📧 Enter your email address to receive test invitation: ").strip()
    
    if not test_email or "@" not in test_email:
        print("❌ Invalid email address")
        return
    
    print(f"\n🚀 Sending test invitation to {test_email}...")
    print(f"📤 Using provider: {os.getenv('EMAIL_PROVIDER')}")
    print(f"📨 From: {os.getenv('EMAIL_FROM')}")
    
    try:
        result = send_invitation_email(
            to_email=test_email,
            invite_link="http://localhost:5173/accept-invite?token=test-token-12345",
            inviter_name="Test Admin",
            workspace_name="Demo Workspace"
        )
        
        print("\n✅ Email sent successfully!")
        print(f"📊 Result: {result}")
        print(f"\n📬 Check {test_email} for the invitation email")
        print("   (Check spam folder if not in inbox)")
        
    except ImportError as e:
        print(f"\n❌ Missing dependency: {e}")
        print("\nTo fix this, run:")
        print("pip install resend")
        
    except ValueError as e:
        print(f"\n❌ Configuration error: {e}")
        print("\nMake sure you've set:")
        print("- RESEND_API_KEY (or EMAIL_API_KEY)")
        print("- EMAIL_FROM (optional, defaults to onboarding@resend.dev)")
        
    except Exception as e:
        print(f"\n❌ Error sending email: {e}")
        print("\nTroubleshooting:")
        print("1. Verify your API key is correct")
        print("2. Check if you're using the right email provider")
        print("3. Make sure sender email is verified (or use onboarding@resend.dev for testing)")


if __name__ == "__main__":
    print("=" * 60)
    print("🔧 CogniSim AI - Email Invitation Test")
    print("=" * 60)
    main()
