#!/bin/bash

# Stop on any error
set -e

echo "Starting deployment..."

cd ~/${REPO_NAME}

if [ ! -d "venv" ] && [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3.10 -m venv .venv
fi

# Activate the environment
source .venv/bin/activate

# Install dependencies
#pip install --upgrade pip
#pip install -r requirements.txt

# Stop existing server if running
pkill -f "uvicorn" || true

sudo systemctl daemon-reload
sudo systemctl restart yolo.service
sudo systemctl enable yolo.service

if ! systemctl is-active --quiet yolo.service; then
  echo "❌ yolo.service is not running."
  sudo systemctl status yolo.service --no-pager
  exit 1
fi
