# Orgteh Infra

## Overview
A FastAPI-based web application providing AI model access (via NVIDIA API), user authentication, subscription management, and developer tools. Originally deployed on Vercel, now running on Replit.

## Tech Stack
- **Language**: Python 3.12
- **Framework**: FastAPI with Jinja2 templates
- **Server**: Uvicorn on port 5000
- **Database**: Upstash Redis (external)
- **AI Backend**: NVIDIA API for model inference
- **Auth**: Session-based with email verification, GitHub OAuth
- **CSS**: Tailwind CSS (Node.js devDependency)

## Project Structure
- `main.py` - FastAPI app entry point with all routes
- `database.py` - Redis database operations (Upstash)
- `services/` - Auth, providers, subscriptions, rate limiting, payments
- `templates/` - Jinja2 HTML templates
- `static/` - Static assets (images, CSS, fonts, model descriptions)
- `tools/` - Tool integrations (finance, RSS, NVIDIA engine)
- `advanced_code_processor/` - AI code processing agents
- `customer_service.py` - Customer service chat router
- `code_processor.py` - Code processing/merge functionality

## Environment Variables Required
- `UPSTASH_URL` / `UPSTASH_TOKEN` - Redis database
- `NVIDIA_API_KEYS` - AI model API keys
- `SESSION_SECRET_KEY` - Session encryption
- `SMTP_HOST` / `SMTP_PORT` / `SMTP_EMAIL` / `SMTP_PASSWORD` - Email
- `TURNSTILE_SECRET_KEY` / `TURNSTILE_SITE_KEY` - Cloudflare captcha
- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` - GitHub OAuth

## Running
```bash
python main.py
```
Server starts on 0.0.0.0:5000 by default.

## Deployment
- Target: Autoscale
- Build: `pip install -r requirements.txt`
- Run: `python -m uvicorn main:app --host 0.0.0.0 --port 5000`

## Recent Changes
- 2026-02-23: Configured for Replit environment (port 5000, X-Frame-Options ALLOWALL for proxy iframe)
