#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# AppraisalIQ — DigitalOcean Droplet Deploy Script
# Run on your droplet after cloning the repo:
#   sudo bash scripts/deploy_do.sh
# ──────────────────────────────────────────────────────────────────────────────
set -e

echo "🚀 AppraisalIQ — DigitalOcean Deployment"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "📦 Installing system packages..."
apt-get update -q
apt-get install -y -q docker.io docker-compose-plugin curl ufw

# ── 2. Docker service ─────────────────────────────────────────────────────────
systemctl enable --now docker
echo "✅ Docker running"

# ── 3. Firewall ───────────────────────────────────────────────────────────────
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
echo "✅ Firewall configured (22, 80, 443)"

# ── 4. Env file check ─────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "⚠  IMPORTANT: Edit .env before continuing!"
  echo "   nano .env"
  echo ""
  echo "   Required settings:"
  echo "   - ANTHROPIC_API_KEY=sk-ant-..."
  echo "   - POSTGRES_PASSWORD=<strong_password>"
  echo "   - SECRET_KEY=<run: python3 -c \"import secrets; print(secrets.token_hex(32))\">"
  echo "   - ENVIRONMENT=production"
  echo "   - CORS_ORIGINS=https://yourdomain.com"
  echo ""
  read -p "Press Enter after editing .env to continue..."
fi

# ── 5. SSL placeholder ────────────────────────────────────────────────────────
mkdir -p nginx/ssl
if [ ! -f nginx/ssl/fullchain.pem ]; then
  echo "⚠  No SSL cert found at nginx/ssl/fullchain.pem"
  echo "   For free SSL with Let's Encrypt:"
  echo "   1. Install certbot: apt-get install -y certbot"
  echo "   2. certbot certonly --standalone -d yourdomain.com"
  echo "   3. cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem nginx/ssl/"
  echo "   4. cp /etc/letsencrypt/live/yourdomain.com/privkey.pem nginx/ssl/"
  echo ""
  echo "   Running WITHOUT SSL for now (HTTP only)..."
  PROD_PROFILE=""
else
  PROD_PROFILE="--profile production"
fi

# ── 6. Build & start ─────────────────────────────────────────────────────────
echo "🐳 Building and starting production stack..."
docker compose $PROD_PROFILE up --build -d

echo ""
echo "✅ Deployment complete!"
echo ""
if [ -n "$PROD_PROFILE" ]; then
  echo "   App running at: https://yourdomain.com"
else
  echo "   App running at: http://$(curl -s ifconfig.me)"
  echo "   ⚠  Add SSL for production use!"
fi
echo ""
echo "   Default login: admin@appraisaliq.local / AdminPass1!"
echo "   Change the admin password immediately after login."
echo ""
echo "📋 View logs: docker compose logs -f"
echo "🔄 Update: git pull && docker compose up --build -d"
echo "🛑 Stop:   docker compose down"
