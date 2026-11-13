"""Run all checks, requiring a real transaction-capable MongoDB test server."""
import os
from pathlib import Path
import sys
import unittest


def main():
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("Install requirements first: python -m pip install -r requirements.txt", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env", override=False)
    if not os.getenv("TEST_MONGODB_URI"):
        print("Set TEST_MONGODB_URI to a replica-set/Atlas connection. Tests create and remove only random shopsync_uc4_test_* databases.", file=sys.stderr)
        return 2
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), top_level_dir=None)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print("Live verification is incomplete because tests were skipped.", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
