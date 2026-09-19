"""Module entry point for python -m tests.stage2."""
from test_stage2 import Stage2TestSuite
import sys

if __name__ == "__main__":
    suite = Stage2TestSuite()
    sys.exit(suite.run_all())
