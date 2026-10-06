#!/bin/sh
set -eu
# Installs only a symlink in a directory explicitly chosen by the user.
if [ "$#" -ne 1 ]; then
  echo 'Usage: ./install.sh /absolute/path/to/bin' >&2
  exit 2
fi
case "$1" in /*) ;; *) echo 'Use an absolute directory path' >&2; exit 2 ;; esac
tool_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$1"
if [ -e "$1/dev" ] || [ -L "$1/dev" ]; then
  echo "Refusing to overwrite $1/dev" >&2
  exit 1
fi
ln -s "$tool_dir/dev" "$1/dev"
echo "Installed $1/dev"
echo "Add this directory to PATH in your shell, then run dev --doctor."
