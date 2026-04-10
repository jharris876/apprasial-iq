# AppraisalIQ — AI Appraisal Review Platform

> Automated USPAP 2024, Fannie Mae, and Freddie Mac compliance review powered by Claude AI.

---

## What's Included

```
appraisaliq/
├── backend/                  FastAPI Python backend
│   ├── main.py               App entry point
│   ├── core/
│   │   ├── config.py         Settings (pydantic-settings + .env)
│   │   └── auth.py           JWT authentication
│   ├── db/
│   │   ├── database.py       Async SQLAlchemy engine + session
│   │   └── models.py         ORM models (User, Report, Issue, MathCheck, AuditLog…)
│   ├── api/
│   │   ├── routes.py         All API endpoints
│   │   └── schemas.py        Pydantic request/response schemas
│   ├── services/
│   │   ├── review_engine.py  Core AI review logic (streaming, DB persistence)
│   │   └── extractor.py      PDF / DOCX / TXT text extraction
│   ├── alembic/              Database migrations
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── index.html            Full single-page app (vanilla JS, no build step)
│   ├── nginx.conf            Frontend nginx config
│   └── Dockerfile
├── nginx/
│   └── nginx.conf            Production reverse proxy + SSL
├── scripts/
│   ├── init.sql              PostgreSQL schema + seed data
│   ├── dev.sh                Local dev startup script
│   └── deploy_do.sh          DigitalOcean deployment script
├── docker-compose.yml        Full stack orchestration
└── .env.example              Environment variable template
```

---

## Quick Start — Local Development

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- An [Anthropic API key](https://console.anthropic.com/)

### Steps

**1. Clone / copy the project**
```bash
cd appraisaliq
```

**2. Create your `.env` file**
```bash
cp .env.example .env
```
Then edit `.env` and fill in:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
POSTGRES_PASSWORD=choose_a_strong_password
SECRET_KEY=run_python_below_to_generate
```

Generate a secret key:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

**3. Start everything**
```bash
bash scripts/dev.sh
```

Or manually:
```bash
docker compose up --build -d db backend frontend
```

**4. Open the app**
- Frontend: http://localhost:3000
- API Docs: http://localhost:8000/api/docs
- Default login: `admin@appraisaliq.local` / `AdminPass1!`

**5. Run a review**
1. Sign in
2. Click **New Review**
3. Click **Load Sample Report** or paste/upload your own
4. Click **Run Appraisal Review**
5. Watch real-time streaming analysis, then navigate flagged issues

---

## Database

PostgreSQL 16 is used. The schema is initialized automatically via `scripts/init.sql` on first startup.

### Key Tables

| Table | Purpose |
|-------|---------|
| `users` | Appraisers, reviewers, admins |
| `reports` | Uploaded appraisal reports + extracted metadata |
| `issues` | Every flagged deficiency with severity, section, correction |
| `math_checks` | Per-figure recalculation results |
| `audit_log` | Immutable event log for every action |
| `report_revisions` | Score/issue delta tracking across re-reviews |
| `api_keys` | Programmatic access for lender integrations |

### Connect directly
```bash
docker compose exec db psql -U appraisaliq -d appraisaliq
```

Useful queries:
```sql
-- All reports with issue counts
SELECT * FROM report_summary ORDER BY created_at DESC;

-- All math errors found
SELECT * FROM math_errors;

-- Issues by severity
SELECT severity, COUNT(*) FROM issues GROUP BY severity;
```

### Migrations (Alembic)
```bash
docker compose exec backend alembic revision --autogenerate -m "description"
docker compose exec backend alembic upgrade head
```

---

## API Reference

Full interactive docs at http://localhost:8000/api/docs (development mode).

### Auth
```
POST /api/v1/auth/register   Register new user
POST /api/v1/auth/login      Get JWT token
GET  /api/v1/auth/me         Current user info
```

### Reports
```
POST /api/v1/reports/upload              Upload file or paste text
GET  /api/v1/reports                     List your reports
POST /api/v1/reports/{id}/review         Start AI review (SSE streaming)
GET  /api/v1/reports/{id}                Full report with issues + math checks
GET  /api/v1/reports/{id}/status         Processing status
GET  /api/v1/reports/{id}/audit          Audit log for report
DELETE /api/v1/reports/{id}              Delete report + file
```

### Issues
```
PATCH /api/v1/issues/{id}/feedback       Submit confirmed/dismissed/corrected feedback
```

### Admin
```
GET /api/v1/admin/users    List all users
GET /api/v1/admin/stats    Platform statistics
```

---

## DigitalOcean Deployment

### Recommended Droplet
- **Size**: Basic $24/mo (4GB RAM, 2 vCPU) — handles 10–20 concurrent reviews
- **OS**: Ubuntu 22.04 LTS
- **Region**: Closest to your users

### Steps

**1. Create a Droplet** on DigitalOcean, SSH in:
```bash
ssh root@your-droplet-ip
```

**2. Upload the project**
```bash
# Option A: Git
git clone https://your-repo/appraisaliq.git
cd appraisaliq

# Option B: SCP from local machine
scp -r ./appraisaliq root@your-droplet-ip:/root/
```

**3. Run the deploy script**
```bash
sudo bash scripts/deploy_do.sh
```

The script will:
- Install Docker
- Configure the firewall (ports 22, 80, 443)
- Prompt you to fill in `.env`
- Build and start all containers

**4. Add SSL (recommended)**
```bash
apt-get install -y certbot
certbot certonly --standalone -d yourdomain.com
cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem nginx/ssl/
cp /etc/letsencrypt/live/yourdomain.com/privkey.pem nginx/ssl/
docker compose --profile production up --build -d
```

**5. Set up auto-renew for SSL**
```bash
crontab -e
# Add: 0 12 * * * certbot renew --quiet && docker compose restart nginx
```

### Update the app
```bash
git pull
docker compose up --build -d
```

### View logs
```bash
docker compose logs -f backend    # API logs
docker compose logs -f db         # Database logs
docker compose logs -f frontend   # Nginx logs
```

---

## Architecture

```
Browser
  │
  ├─► http://localhost:3000  (Frontend — nginx serving index.html)
  │         │
  │         └─► http://localhost:8000/api/v1  (Backend — FastAPI)
  │                     │
  │                     ├─► PostgreSQL:5432  (Database)
  │                     ├─► /app/uploads     (File storage)
  │                     └─► Anthropic API    (Claude AI)
  │
Production (DigitalOcean):
  Browser ──► nginx:443 (SSL) ──► backend:8000
                               └─► frontend:80
```

### Review Flow
1. User uploads PDF/DOCX or pastes text → `POST /reports/upload`
2. Backend extracts text (PyPDF2 / python-docx)
3. User triggers review → `POST /reports/{id}/review`
4. Backend streams request to Claude claude-sonnet-4-20250514 with USPAP/Fannie/Freddie system prompt
5. Claude returns structured JSON: score, grade, issues[], math_checks[]
6. Backend parses + persists all results to PostgreSQL
7. Frontend receives SSE events and shows live progress
8. Results screen renders issues with severity, correction, rule reference
9. User submits feedback (confirm/dismiss/correct) → stored in DB

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | Your Anthropic API key |
| `POSTGRES_PASSWORD` | ✅ | Database password |
| `SECRET_KEY` | ✅ | JWT signing secret (32+ random chars) |
| `POSTGRES_DB` | No | Database name (default: appraisaliq) |
| `POSTGRES_USER` | No | Database user (default: appraisaliq) |
| `ENVIRONMENT` | No | `development` or `production` |
| `CORS_ORIGINS` | No | Comma-separated allowed origins |
| `MAX_FILE_SIZE_MB` | No | Max upload size (default: 50) |

---

## Security Notes

- Change the default admin password immediately after first login
- Use a strong `POSTGRES_PASSWORD` and `SECRET_KEY` in production
- Enable HTTPS before exposing to the internet
- The `.env` file is in `.gitignore` — never commit it
- API keys in `api_keys` table are bcrypt-hashed

---

## License

Internal use. Contact your team lead for licensing terms.
