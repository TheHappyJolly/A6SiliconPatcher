#!/bin/bash
cd "$(dirname "$0")"
export A6_PYHOME="$PWD/x64"
exec arch -x86_64 native/a6launcher ${A6_MAIN:-n6main.py} "$@"
