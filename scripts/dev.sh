#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# AppraisalIQ — Local Development Startup
# Run this from the project root: ./scripts/dev.sh
# ──────────────────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# ── Check .env ────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  echo "⚠  No .env file found. Copying from .env.example..."
  cp .env.example .env
  echo "✏  Edit .env and add your ANTHROPIC_API_KEY, then re-run this script."
  exit 1
fi

# ── Check Docker ──────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  echo "❌ Docker not found. Install Docker Desktop from https://docker.com"
  exit 1
fi

if ! command -v docker compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
  echo "❌ Docker Compose not found."
  exit 1
fi

echo ""
echo "  ╔═══════════════════════════════════╗"
echo "  ║       AppraisalIQ  v1.0.0         ║"
echo "  ║   USPAP · Fannie Mae · Freddie    ║"
echo "  ╚═══════════════════════════════════╝"
echo ""

# ── Start only db + backend + frontend (no nginx for local dev) ───────────────
echo "🐳 Starting services..."
docker compose up --build -d db backend frontend

echo ""
echo "⏳ Waiting for database..."
sleep 8

echo ""
echo "✅ Services started!"
echo ""
echo "   Frontend:  http://localhost:3000"
echo "   Backend:   http://localhost:8000"
echo "   API Docs:  http://localhost:8000/api/docs"
echo "   DB Port:   localhost:5432"
echo ""
echo "   Default login: admin@appraisaliq.local / AdminPass1!"
echo ""
echo "📋 Logs: docker compose logs -f"
echo "🛑 Stop: docker compose down"
