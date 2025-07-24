#!/usr/bin/env python3
"""
Authenticated API Test Script
Tests the CogniSim AI API endpoints with proper authentication
"""

import requests
import json
from get_token import get_jwt
from supabase import create_client
import os
from dotenv import load_dotenv

# Load environment
load_dotenv()

BASE_URL = "http://127.0.0.1:8000"

def get_auth_token():
    """Get authentication token automatically."""
    try:
        # Use the same credentials as in get_token.py
        email = "hammadahhmed06@gmail.com"
        password = "hammad12"
        
        # Get Supabase client
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_ANON_KEY")
        supabase = create_client(url, key)
        
        # Sign in and get token
        response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if response.session:
            return response.session.access_token
        else:
            print("❌ Failed to get authentication token")
            return None
    except Exception as e:
        print(f"❌ Error getting token: {e}")
        return None

def make_authenticated_request(method, endpoint, data=None):
    """Make an authenticated request to the API."""
    token = get_auth_token()
    if not token:
        return None
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    url = f"{BASE_URL}{endpoint}"
    
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=data)
        else:
            print(f"❌ Unsupported method: {method}")
            return None
        
        return response
    except Exception as e:
        print(f"❌ Request failed: {e}")
        return None

def test_profile():
    """Test the user profile endpoint."""
    print("🔍 Testing User Profile...")
    response = make_authenticated_request("GET", "/api/profile")
    
    if response and response.status_code == 200:
        data = response.json()
        print(f"✅ Profile: {data['email']} (ID: {data['id']})")
        return True
    else:
        print(f"❌ Profile test failed: {response.status_code if response else 'No response'}")
        return False

def test_jira_status():
    """Test Jira integration status."""
    print("\n🔍 Testing Jira Status...")
    response = make_authenticated_request("GET", "/api/integrations/jira/status")
    
    if response:
        print(f"Status Code: {response.status_code}")
        try:
            data = response.json()
            print(f"Response: {json.dumps(data, indent=2)}")
            return response.status_code in [200, 404]  # 404 is OK if no integration exists yet
        except:
            print(f"Response: {response.text}")
            return False
    return False

def test_jira_connection():
    """Test Jira connection."""
    print("\n🔍 Testing Jira Connection...")
    response = make_authenticated_request("GET", "/api/integrations/jira/test")
    
    if response:
        print(f"Status Code: {response.status_code}")
        try:
            data = response.json()
            print(f"Response: {json.dumps(data, indent=2)}")
            return response.status_code in [200, 404]
        except:
            print(f"Response: {response.text}")
            return False
    return False

def test_jira_connect():
    """Test connecting to Jira."""
    print("\n🔍 Testing Jira Connect...")
    
    jira_data = {
        "jira_url": "https://hammadahmed06.atlassian.net",
        "jira_email": "malikxd06@gmail.com",
        "jira_api_token": "ATATT3xFfGF0lfxf-7qZmeJDVQhvGU51PC73dm9J2_HF11misbq4eNVhLXAI0_jKUxPyE0oTztQgzjk2DezOakP8OZYvCfpImR10bOai1sUq9NW9YUQMC3WU5n6dUqmaSQnpQRqFyroYgrCyKWhkraGIBYetZ_t76uZZWEuFP9wmD50O7yzIh4E=92B8D700"
    }
    
    response = make_authenticated_request("POST", "/api/integrations/jira/connect", jira_data)
    
    if response:
        print(f"Status Code: {response.status_code}")
        try:
            data = response.json()
            print(f"Response: {json.dumps(data, indent=2)}")
            return response.status_code == 200
        except:
            print(f"Response: {response.text}")
            return False
    return False

def main():
    """Run all authenticated API tests."""
    print("🚀 CogniSim AI Authenticated API Tests")
    print("=" * 50)
    
    # Test authentication first
    token = get_auth_token()
    if not token:
        print("❌ Authentication failed. Cannot proceed with tests.")
        return
    
    print(f"✅ Authentication successful! Token: {token[:50]}...")
    
    tests = [
        ("User Profile", test_profile),
        ("Jira Status", test_jira_status),
        ("Jira Connection", test_jira_connection),
        ("Jira Connect", test_jira_connect)
    ]
    
    results = {}
    for test_name, test_func in tests:
        try:
            results[test_name] = test_func()
        except Exception as e:
            print(f"❌ {test_name} failed with error: {e}")
            results[test_name] = False
    
    # Summary
    print(f"\n{'='*50}")
    print("📊 TEST SUMMARY")
    print(f"{'='*50}")
    
    passed = sum(results.values())
    total = len(results)
    
    for test_name, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {test_name:<20} {status}")
    
    print(f"\n🎯 Overall Result: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Your APIs are working correctly with authentication.")
    else:
        print("⚠️  Some tests failed. Check the output above for details.")

if __name__ == "__main__":
    main()
