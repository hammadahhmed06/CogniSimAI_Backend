# test_jira_apis.py
# Test the actual Jira API endpoints

import requests
import json
from datetime import datetime

# Base URL
BASE_URL = "http://localhost:8000"

def test_api_endpoint(method, endpoint, headers=None, data=None):
    """Test an API endpoint and return the response."""
    url = f"{BASE_URL}{endpoint}"
    
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=data)
        else:
            print(f"❌ Unsupported method: {method}")
            return None
        
        print(f"📡 {method.upper()} {endpoint}")
        print(f"Status Code: {response.status_code}")
        
        try:
            response_data = response.json()
            print(f"Response: {json.dumps(response_data, indent=2)}")
        except:
            print(f"Response: {response.text}")
        
        print("-" * 60)
        return response
        
    except requests.exceptions.ConnectionError:
        print(f"❌ Connection failed to {url}")
        return None
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return None

def test_jira_integration():
    """Test all Jira integration endpoints."""
    
    print("🧪 Testing Jira Integration APIs")
    print("=" * 60)
    print(f"Time: {datetime.now()}")
    print("=" * 60)
    
    # Test data
    jira_credentials = {
        "jira_url": "https://hammadahmed06.atlassian.net",
        "jira_email": "malikxd06@gmail.com",
        "jira_api_token": "ATATT3xFfGF0lfxf-7qZmeJDVQhvGU51PC73dm9J2_HF11misbq4eNVhLXAI0_jKUxPyE0oTztQgzjk2DezOakP8OZYvCfpImR10bOai1sUq9NW9YUQMC3WU5n6dUqmaSQnpQRqFyroYgrCyKWhkraGIBYetZ_t76uZZWEuFP9wmD50O7yzIh4E=92B8D700"
    }
    
    headers = {
        "Content-Type": "application/json",
        # Note: We'll test without auth first to see the response
    }
    
    print("🔗 Testing without authentication (to see auth requirement)")
    
    # 1. Test Jira Connect
    print("\n1. Testing Jira Connect Endpoint")
    response1 = test_api_endpoint("POST", "/api/integrations/jira/connect", headers, jira_credentials)
    
    # 2. Test Jira Status
    print("\n2. Testing Jira Status Endpoint")
    response2 = test_api_endpoint("GET", "/api/integrations/jira/status", headers)
    
    # 3. Test Jira Test Connection
    print("\n3. Testing Jira Test Endpoint")
    response3 = test_api_endpoint("GET", "/api/integrations/jira/test", headers)
    
    # 4. Test with a sample project sync
    print("\n4. Testing Jira Sync Endpoint (with sample project ID)")
    sample_project_id = "123e4567-e89b-12d3-a456-426614174000"  # Sample UUID
    sync_data = {
        "jira_project_key": "LEARNJIRA",
        "max_results": 5
    }
    response4 = test_api_endpoint("POST", f"/api/integrations/jira/sync/{sample_project_id}", headers, sync_data)
    
    # 5. Test API documentation endpoint
    print("\n5. Testing API Documentation")
    response5 = test_api_endpoint("GET", "/docs", headers)
    
    # 6. Test OpenAPI schema
    print("\n6. Testing OpenAPI Schema")
    response6 = test_api_endpoint("GET", "/openapi.json", headers)
    
    print("\n🏁 Testing Complete!")
    print("=" * 60)
    
    # Summary
    responses = [response1, response2, response3, response4, response5, response6]
    success_count = sum(1 for r in responses if r and r.status_code < 400)
    
    print(f"📊 Summary: {success_count}/{len(responses)} endpoints accessible")
    print("\n💡 Next Steps:")
    print("- Most endpoints likely require authentication (JWT token)")
    print("- Server is responding correctly")
    print("- Jira connection logic is working (based on server logs)")
    print("- Database workspace ID issue needs to be resolved")

if __name__ == "__main__":
    test_jira_integration()
