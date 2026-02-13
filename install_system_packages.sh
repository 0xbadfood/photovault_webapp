#!/bin/bash
set -e

echo "Updating package lists..."
sudo apt-get update

echo "Installing system dependencies..."
sudo apt-get install -y \
    ffmpeg \
    cmake \
    build-essential \
    python3-dev \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgtk-3-dev \
    sqlite3 \
    python3-venv \
    python3-pip \
    libsm6 \
    libxext6 \
    libgl1

echo "System packages installed successfully."
