#!/usr/bin/env python3
"""
Test script for Token Encryption Service
Tests the core encryption functionality before integration.
"""

import sys
import os
from pathlib import Path

# Add the parent directory to the path so we can import app modules
sys.path.append(str(Path(__file__).parent.parent))

from app.services.encryption.token_encryption import TokenEncryptionService, get_token_encryption_service

def test_basic_encryption():
    """Test basic encryption and decryption functionality."""
    print("🔧 Testing Token Encryption Service")
    print("=" * 50)
    
    try:
        # Initialize the service
        service = TokenEncryptionService()
        print("✅ Encryption service initialized successfully")
        
        # Test data
        test_tokens = [
            "ATATT3xFfGF0abc123XYZ",
            "simple-test-token-123",
            "complex!@#$%^&*()token-with-symbols",
            "very-long-token-that-exceeds-normal-length-to-test-boundary-conditions-and-ensure-proper-handling"
        ]
        
        print(f"\n🔍 Testing with {len(test_tokens)} different token formats...")
        
        for i, original_token in enumerate(test_tokens, 1):
            print(f"\nTest {i}: Token length {len(original_token)} characters")
            
            # Encrypt the token
            encrypted_token = service.encrypt(original_token)
            print(f"  ✅ Encryption successful")
            print(f"  📝 Original length: {len(original_token)}")
            print(f"  📝 Encrypted length: {len(encrypted_token)}")
            
            # Verify it's different from original
            assert encrypted_token != original_token, "Encrypted token should be different from original"
            print(f"  ✅ Encrypted token is different from original")
            
            # Decrypt the token
            decrypted_token = service.decrypt(encrypted_token)
            print(f"  ✅ Decryption successful")
            
            # Verify it matches the original
            assert decrypted_token == original_token, "Decrypted token should match original"
            print(f"  ✅ Decrypted token matches original")
            
            # Test encryption detection
            is_encrypted = service.is_encrypted(encrypted_token)
            is_not_encrypted = service.is_encrypted(original_token)
            assert is_encrypted, "Should detect encrypted token"
            assert not is_not_encrypted, "Should not detect plaintext as encrypted"
            print(f"  ✅ Encryption detection working correctly")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_error_handling():
    """Test error handling for edge cases."""
    print(f"\n🔍 Testing Error Handling...")
    
    try:
        service = TokenEncryptionService()
        
        # Test empty token
        try:
            service.encrypt("")
            print("❌ Should have failed for empty token")
            return False
        except ValueError:
            print("✅ Correctly rejected empty token")
        
        # Test invalid encrypted token
        try:
            service.decrypt("invalid-token")
            print("❌ Should have failed for invalid encrypted token")
            return False
        except ValueError:
            print("✅ Correctly rejected invalid encrypted token")
        
        # Test non-string input
        try:
            service.encrypt(123)
            print("❌ Should have failed for non-string input")
            return False
        except TypeError:
            print("✅ Correctly rejected non-string input")
        
        return True
        
    except Exception as e:
        print(f"❌ Error handling test failed: {str(e)}")
        return False

def test_singleton_service():
    """Test the singleton service function."""
    print(f"\n🔍 Testing Singleton Service...")
    
    try:
        # Get service instances
        service1 = get_token_encryption_service()
        service2 = get_token_encryption_service()
        
        # Should be the same instance
        assert service1 is service2, "Should return the same instance"
        print("✅ Singleton service working correctly")
        
        # Should work normally
        test_token = "test-singleton-token"
        encrypted = service1.encrypt(test_token)
        decrypted = service2.decrypt(encrypted)
        
        assert decrypted == test_token, "Should decrypt correctly"
        print("✅ Singleton service encrypts/decrypts correctly")
        
        return True
        
    except Exception as e:
        print(f"❌ Singleton test failed: {str(e)}")
        return False

def test_jira_token_format():
    """Test with actual Jira token format."""
    print(f"\n🔍 Testing with Jira Token Format...")
    
    try:
        service = TokenEncryptionService()
        
        # Real Jira token format (example)
        jira_token = "ATATT3xFfGF0lfxf-7qZmeJDVQhvGU51PC73dm9J2_HF11misbq4eNVhLXAI0_jKUxPyE0oTztQgzjk2DezOakP8OZYvCfpImR10bOai1sUq9NW9YUQMC3WU5n6dUqmaSQnpQRqFyroYgrCyKWhkraGIBYetZ_t76uZZWEuFP9wmD50O7yzIh4E=92B8D700"
        
        print(f"  📝 Testing with Jira token (length: {len(jira_token)})")
        
        # Encrypt
        encrypted = service.encrypt(jira_token)
        print("  ✅ Jira token encrypted successfully")
        
        # Decrypt
        decrypted = service.decrypt(encrypted)
        print("  ✅ Jira token decrypted successfully")
        
        # Verify
        assert decrypted == jira_token, "Decrypted Jira token should match original"
        print("  ✅ Jira token integrity verified")
        
        return True
        
    except Exception as e:
        print(f"❌ Jira token test failed: {str(e)}")
        return False

def main():
    """Run all encryption tests."""
    print("🚀 Token Encryption Service Test Suite")
    print("=" * 60)
    
    tests = [
        ("Basic Encryption", test_basic_encryption),
        ("Error Handling", test_error_handling),
        ("Singleton Service", test_singleton_service),
        ("Jira Token Format", test_jira_token_format)
    ]
    
    results = {}
    for test_name, test_func in tests:
        try:
            results[test_name] = test_func()
        except Exception as e:
            print(f"❌ {test_name} failed with error: {e}")
            results[test_name] = False
    
    # Summary
    print(f"\n{'='*60}")
    print("📊 TEST SUMMARY")
    print(f"{'='*60}")
    
    passed = sum(results.values())
    total = len(results)
    
    for test_name, success in results.items():
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {test_name:<20} {status}")
    
    print(f"\n🎯 Overall Result: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Token encryption service is ready.")
        print("\n📋 Next Steps:")
        print("  1. Integrate with Jira client")
        print("  2. Update credential storage")
        print("  3. Create data migration script")
    else:
        print("⚠️  Some tests failed. Check the output above for details.")
        return False
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
