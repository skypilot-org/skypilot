#!/bin/bash

rm -rf build docs
make html
cp -r build/html docs
