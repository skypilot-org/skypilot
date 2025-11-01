#! /usr/bin/env bash

set -e

rm -rf dist/*
python -m build
twine upload dist/*

VERSION=$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
