#!/bin/bash
pytest -s -n 16 -q --tb=short --disable-warnings tests/test_smoke.py

# To run all tests including the slow ones, add the --runslow flag:
# pytest --runslow -s -n 16 -q --tb=short --disable-warnings tests/test_smoke.py
