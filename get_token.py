# get_token.py
import os
from dotenv import load_dotenv
from supabase import create_client, Client
import asyncio

# Use the PUBLIC ANON KEY for signing in users
load_dotenv()
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_ANON_KEY") # Use the ANON key, not the service key

if not url or not key:
    raise ValueError("Please set SUPABASE_URL and SUPABASE_ANON_KEY in your .env file")

supabase: Client = create_client(url, key)

def get_jwt(email, password):
    try:
        print(f"Attempting to sign in as {email}...")
        response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        session = response.session
        if session:
            print("\n--- Login Successful! ---")
            print(f"Access Token for {email}:")
            print(session.access_token)
            print("\nCopy the token above to use in your API tests.")
        else:
            print("Login failed. Check email/password and user existence in Supabase.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    # IMPORTANT: Use a real password you've set for your seed users.
    # Go to Supabase > Authentication > Users and set a password for alice@cognisim.dev
    user_email = "hammadahhmed06@gmail.com"
    user_password = "hammad12" # <-- CHANGE THIS
    get_jwt(user_email, user_password)