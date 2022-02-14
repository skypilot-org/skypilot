#!/bin/bash

rm -rf build/html docs
make html
cp -r build/html docs
