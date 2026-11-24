#!/usr/bin/env bash
# Starts the backend API and the frontend dev server.
# First run: creates the Python venv and installs dependencies.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d backend/.venv ]; then
  python3 -m venv backend/.venv
fi
backend/.venv/bin/python -m pip install -q -r backend/requirements.txt

if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  echo "Created backend/.env - fill in SMTP_* and COMPANY_ORG_NUMBER before sending real emails."
fi

if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm install)
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

(cd backend && .venv/bin/python -m uvicorn app.main:app_factory --factory --port 8000) &
(cd frontend && npm run dev) &
echo "Open http://localhost:5173"
wait
