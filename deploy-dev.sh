#!/bin/bash

# Stop on any error
set -e

echo "Starting deployment..."

pwd
git checkout dev

if [ ! -d "venv" ] && [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3.10 -m venv .venv
fi

# Activate the environment
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install -r torch-requirements.txt


# Stop existing server if running
pkill -f "uvicorn" || true

sudo systemctl daemon-reload
sudo systemctl restart yolo.service
sudo systemctl enable yolo.service

sudo systemctl restart otelcol

if ! systemctl is-active --quiet yolo.service; then
  echo "❌ yolo.service is not running."
  sudo systemctl status yolo.service --no-pager
  exit 1
fi

if ! systemctl is-active --quiet otelcol.service; then
  echo "❌ yolo.service is not running."
  sudo systemctl status otelcol.service --no-pager
  exit 1
fi
