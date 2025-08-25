#!/usr/bin/env python3
"""
Quick test runner - executes the main test script
"""
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    script_path = Path(__file__).parent / "scripts" / "run_tests.py"
    sys.exit(subprocess.call([sys.executable, str(script_path)]))