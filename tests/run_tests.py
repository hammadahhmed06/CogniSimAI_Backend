#!/usr/bin/env python3
"""
Test Runner for CogniSim AI Jira Integration
Runs all available tests in the tests directory.
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path

def run_test(test_file: str, test_name: str) -> bool:
    """Run a single test file and return success status."""
    print(f"\n{'='*60}")
    print(f"🧪 Running {test_name}")
    print(f"{'='*60}")
    
    try:
        # Change to the parent directory to run tests
        parent_dir = Path(__file__).parent.parent
        result = subprocess.run(
            [sys.executable, f"tests/{test_file}"],
            cwd=parent_dir,
            capture_output=False,
            text=True
        )
        
        if result.returncode == 0:
            print(f"✅ {test_name} completed successfully")
            return True
        else:
            print(f"❌ {test_name} failed with exit code {result.returncode}")
            return False
            
    except Exception as e:
        print(f"❌ Error running {test_name}: {e}")
        return False

def main():
    """Main test runner function."""
    parser = argparse.ArgumentParser(description="Run CogniSim AI Jira Integration Tests")
    parser.add_argument(
        "--test", 
        choices=["jira", "database", "api", "direct", "all"],
        default="all",
        help="Which test to run (default: all)"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available tests"
    )
    
    args = parser.parse_args()
    
    # Available tests
    tests = {
        "jira": ("test_jira_integration.py", "Jira Integration Test"),
        "database": ("test_database_fix.py", "Database Fix Test"),
        "api": ("test_api_endpoints.py", "API Endpoints Test"),
        "direct": ("test_direct_integration.py", "Direct Integration Test")
    }
    
    if args.list:
        print("📋 Available Tests:")
        print("-" * 30)
        for key, (file, name) in tests.items():
            print(f"  {key:10} - {name}")
        print("\nUsage: python run_tests.py --test <test_name>")
        print("       python run_tests.py --test all  (runs all tests)")
        return
    
    print("🚀 CogniSim AI Jira Integration Test Suite")
    print("=" * 60)
    
    if args.test == "all":
        print("Running all available tests...")
        
        results = {}
        for key, (file, name) in tests.items():
            results[key] = run_test(file, name)
        
        # Summary
        print(f"\n{'='*60}")
        print("📊 TEST SUMMARY")
        print(f"{'='*60}")
        
        passed = sum(results.values())
        total = len(results)
        
        for key, success in results.items():
            status = "✅ PASS" if success else "❌ FAIL"
            print(f"  {tests[key][1]:<30} {status}")
        
        print(f"\n🎯 Overall Result: {passed}/{total} tests passed")
        
        if passed == total:
            print("🎉 All tests passed! Your integration is working correctly.")
        else:
            print("⚠️  Some tests failed. Check the output above for details.")
            
    else:
        # Run single test
        if args.test in tests:
            file, name = tests[args.test]
            success = run_test(file, name)
            
            if success:
                print(f"\n🎉 {name} completed successfully!")
            else:
                print(f"\n❌ {name} failed!")
                sys.exit(1)
        else:
            print(f"❌ Unknown test: {args.test}")
            print("Use --list to see available tests")
            sys.exit(1)

if __name__ == "__main__":
    main()
