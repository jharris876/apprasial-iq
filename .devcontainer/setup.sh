#!/bin/bash
echo "Setting up AppraisalIQ dev environment..."

# Install PostgreSQL client tools
sudo apt-get update -q && sudo apt-get install -y -q postgresql-client

# Start PostgreSQL service
sudo service postgresql start 2>/dev/null || true

# Wait for postgres to be ready
echo "Waiting for PostgreSQL..."
for i in {1..30}; do
  pg_isready -h localhost -U postgres 2>/dev/null && break
  sleep 1
done

# Create the database and user
sudo -u postgres psql <<SQL
CREATE USER appraisaliq WITH PASSWORD 'devpassword';
CREATE DATABASE appraisaliq OWNER appraisaliq;
GRANT ALL PRIVILEGES ON DATABASE appraisaliq TO appraisaliq;
SQL

# Run the schema
PGPASSWORD=devpassword psql -h localhost -U appraisaliq -d appraisaliq \
  -f scripts/init.sql 2>/dev/null && echo "Schema loaded successfully" || echo "Schema may already exist, continuing..."

# Create backend .env if it doesn't exist
if [ ! -f backend/.env ]; then
  cp .env.example backend/.env
  echo "Created backend/.env - add your ANTHROPIC_API_KEY to Codespaces secrets"
fi

echo ""
echo "✅ Setup complete!"
echo "To start the backend run:"
echo "  cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000"
