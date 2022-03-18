#!/bin/bash
time pytest -s -n 16 -q --tb=short --disable-warnings tests/test_smoke.py
