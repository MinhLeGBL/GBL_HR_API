#!/usr/bin/env python3
"""
Test runner script for GBL HR API
"""
import sys
import subprocess
from pathlib import Path

def run_command(cmd, description):
    """Run a command and return success status"""
    print(f"\n🔄 {description}")
    print(f"Running: {cmd}")
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    if result.returncode == 0:
        print(f"✅ {description} - SUCCESS")
        if result.stdout:
            print(result.stdout)
        return True
    else:
        print(f"❌ {description} - FAILED")
        if result.stderr:
            print(result.stderr)
        if result.stdout:
            print(result.stdout)
        return False

def main():
    """Main test runner"""
    print("🧪 GBL HR API Test Suite")
    print("=" * 50)
    
    # Test commands
    commands = [
        ("python -m pytest tests/unit/ -v", "Unit Tests"),
        ("python -m pytest tests/integration/ -v -m integration", "Integration Tests"), 
        ("python tests/utils/test_connection.py", "Database Connection Test"),
        ("python -m pytest tests/ --cov=app --cov-report=term-missing", "Coverage Report")
    ]
    
    results = []
    
    for cmd, desc in commands:
        success = run_command(cmd, desc)
        results.append((desc, success))
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 TEST SUMMARY")
    print("=" * 50)
    
    all_passed = True
    for desc, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{desc:.<40} {status}")
        if not success:
            all_passed = False
    
    print("\n" + "=" * 50)
    if all_passed:
        print("🎉 ALL TESTS PASSED!")
        return 0
    else:
        print("💥 SOME TESTS FAILED!")
        return 1

if __name__ == "__main__":
    sys.exit(main())