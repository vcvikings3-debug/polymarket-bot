#!/usr/bin/env python3
"""Health check — verifies Python version, required packages, and folder structure."""

import sys
import os
import importlib

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)

PASS = 0
FAIL = 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  PASS  | {name}")
        PASS += 1
    else:
        msg = f"  FAIL  | {name}"
        if detail:
            msg += f"  ({detail})"
        print(msg)
        FAIL += 1

def main():
    global PASS, FAIL
    print("=" * 60)
    print("  Polymarket Bot — Health Check")
    print("=" * 60)

    # Python version
    print("\n[Python Version]")
    py_version = sys.version_info
    check("Python 3.12+", py_version.major == 3 and py_version.minor >= 12,
          f"got {py_version.major}.{py_version.minor}.{py_version.micro}")

    # Required packages
    print("\n[Required Packages]")
    packages = [
        "requests", "dotenv", "flask", "sqlalchemy", "schedule",
        "websocket", "py_clob_client", "web3", "pandas", "numpy",
        "ollama", "loguru", "pytest",
    ]
    for pkg in packages:
        try:
            importlib.import_module(pkg)
            check(pkg, True)
        except ImportError:
            check(pkg, False, "not importable")

    # Folder structure
    print("\n[Folder Structure]")
    required_dirs = [
        "data", "intelligence", "decision", "execution",
        "dashboard", os.path.join("dashboard", "templates"),
        "logs", "tests",
    ]
    for d in required_dirs:
        path = os.path.join(PROJECT_ROOT, d)
        check(f"Folder '{d}'", os.path.isdir(path))

    # Key files
    print("\n[Key Files]")
    required_files = [
        ".env.example", "requirements.txt", "config.py", "main.py",
        "intelligence/news_analyzer.py", "intelligence/market_scorer.py",
        "intelligence/sentiment_engine.py",
        "decision/bet_engine.py", "decision/risk_manager.py",
        "execution/polymarket_client.py", "execution/wallet.py",
        "dashboard/app.py",
        "data/database.py",
    ]
    for f in required_files:
        path = os.path.join(PROJECT_ROOT, f)
        check(f"File '{f}'", os.path.isfile(path))

    # Summary
    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"  Results: {PASS}/{total} passed, {FAIL}/{total} failed")
    print("=" * 60)

    return 1 if FAIL > 0 else 0

if __name__ == "__main__":
    sys.exit(main())