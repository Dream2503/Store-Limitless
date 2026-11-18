#!/bin/bash

set -e

python -m pip install --quiet build

rm -rf dist build *.egg-info

python -m build