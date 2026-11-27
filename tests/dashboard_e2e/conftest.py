"""Dashboard e2e suite for the OSS dashboard. See README.md."""
import os
import sys

# Make `framework` and this suite's modules importable from the tests.
sys.path.insert(0, os.path.dirname(__file__))

pytest_plugins = ['framework.fixtures']
