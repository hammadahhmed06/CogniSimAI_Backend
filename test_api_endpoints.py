#!/usr/bin/env python3
"""
Complete API Integration Test for CogniSim AI Jira Integration
Tests all endpoints to ensure the integration is working properly.
"""

import requests
import json
import sys
from typing import Dict, Any

# Configuration
BASE_URL = "http://127.0.0.1:8000"
TEST_USER_TOKEN = "your_jwt_token_here"  # You'll need to replace this with actual token

# Test data for Jira connection
JIRA_TEST_DATA = {
    "jira_url": "https://hammadahmed06.atlassian.net",
    "jira_email": "malikxd06@gmail.com",
    "jira_api_token": "ATATT3xFfGF0yPFHgEFfOXu4s6JlkP8fEP0vdpjvCbJZvUMhVX8k6wKITBwI3aR7cWxRPdXVv1Eg5pGV0HNJ_PrXf4tJ4GiRJ-F9JlpWC2cBxNr4qUNqw-pJ4hP8aWJlOJJhHFd5p9Kj4dFgEt0HXqYvZJBd5Q_DzGM-rNJzD3Q=xX4B2F9A"
}

def make_request(method: str, endpoint: str, data: Dict[Any, Any] = None, headers: Dict[str, str] = None) -> requests.Response:
    """Make HTTP request with proper error handling."""
    url = f"{BASE_URL}{endpoint}"
    default_headers = {"Content-Type": "application/json"}
    
    if headers:
        default_headers.update(headers)
    
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=default_headers)
        elif method.upper() == "POST":
            response = requests.post(url, json=data, headers=default_headers)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        return response
    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {e}")
        return None

def test_health_check():
    """Test the basic health check endpoint."""
    print("🔍 Testing Health Check...")
    response = make_request("GET", "/")
    
    if response and response.status_code == 200:
        data = response.json()
        print(f"✅ Health Check: {data.get('status')}")
        return True
    else:
        print(f"❌ Health Check failed: {response.status_code if response else 'No response'}")
        return False

def test_jira_endpoints_without_auth():
    """Test Jira endpoints without authentication to see expected behavior."""
    print("\n🔍 Testing Jira Endpoints (without auth)...")
    
    endpoints = [
        "/api/integrations/jira/status",
        "/api/integrations/jira/test"
    ]
    
    for endpoint in endpoints:
        response = make_request("GET", endpoint)
        print(f"   {endpoint}: {response.status_code if response else 'No response'} - {response.json().get('detail', 'No detail') if response and hasattr(response, 'json') else 'N/A'}")

def test_jira_connect_without_auth():
    """Test Jira connection endpoint without authentication."""
    print("\n🔍 Testing Jira Connect (without auth)...")
    
    response = make_request("POST", "/api/integrations/jira/connect", data=JIRA_TEST_DATA)
    
    if response:
        print(f"   Connect endpoint: {response.status_code}")
        try:
            result = response.json()
            print(f"   Response: {result.get('detail', result)}")
        except:
            print(f"   Response: {response.text}")

def test_api_docs():
    """Test that API documentation is accessible."""
    print("\n🔍 Testing API Documentation...")
    
    response = make_request("GET", "/docs")
    if response and response.status_code == 200:
        print("✅ API Documentation is accessible")
        return True
    else:
        print(f"❌ API Documentation failed: {response.status_code if response else 'No response'}")
        return False

def test_openapi_spec():
    """Test that OpenAPI specification is accessible."""
    print("\n🔍 Testing OpenAPI Specification...")
    
    response = make_request("GET", "/openapi.json")
    if response and response.status_code == 200:
        try:
            spec = response.json()
            print(f"✅ OpenAPI Spec: {spec.get('info', {}).get('title', 'Unknown')}")
            
            # Count available endpoints
            paths = spec.get('paths', {})
            jira_paths = [path for path in paths.keys() if 'jira' in path.lower()]
            print(f"   📊 Total endpoints: {len(paths)}")
            print(f"   🔗 Jira endpoints: {len(jira_paths)}")
            
            if jira_paths:
                print("   🎯 Available Jira endpoints:")
                for path in jira_paths:
                    methods = list(paths[path].keys())
                    print(f"      {path} ({', '.join(methods).upper()})")
            
            return True
        except json.JSONDecodeError:
            print("❌ Invalid JSON in OpenAPI spec")
            return False
    else:
        print(f"❌ OpenAPI Spec failed: {response.status_code if response else 'No response'}")
        return False

def main():
    """Run all tests."""
    print("🚀 Starting CogniSim AI Jira Integration API Tests")
    print("=" * 60)
    
    tests = [
        test_health_check,
        test_api_docs,
        test_openapi_spec,
        test_jira_endpoints_without_auth,
        test_jira_connect_without_auth
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"❌ Test {test.__name__} failed with error: {e}")
    
    print("\n" + "=" * 60)
    print(f"📊 Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Your Jira integration API is working correctly.")
    else:
        print("⚠️  Some tests failed. Check the output above for details.")
    
    print("\n💡 Next Steps:")
    print("   1. Open http://127.0.0.1:8000/docs in your browser")
    print("   2. Use a valid JWT token to test authenticated endpoints")
    print("   3. Try the /api/integrations/jira/connect endpoint with your Jira credentials")
    print("   4. Test the /api/integrations/jira/test endpoint to verify connection")

if __name__ == "__main__":
    main()
