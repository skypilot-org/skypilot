#!/bin/bash

rm -rf build/html docs
make html
mv build/html docs
