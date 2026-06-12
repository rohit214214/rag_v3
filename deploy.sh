#!/bin/bash
set -e

cd /home/rag_v3

git fetch origin master
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/master)

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "$(date): No changes, skipping"
    exit 0
fi

echo "$(date): New changes found — deploying..."
git pull origin master
docker compose down
docker compose up -d --build
echo "$(date): Done"