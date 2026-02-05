#!/usr/bin/env bash
set -e
WORKDIR=/home/admin/chat_anon_ws

cd "$WORKDIR/chat_anon_frontend" && bash dev-https.sh &
cd "$WORKDIR/chat_anon_backend" && source venv/bin/activate && exec python3 -m server.main
