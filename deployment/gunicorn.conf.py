# ==============================================================================
# Ultimate Smile Design - AI Calling Agent
# Production Gunicorn Configuration for aaPanel (Uvicorn Worker)
#
# Usage:
#   gunicorn -c deployment/gunicorn.conf.py backend.app.main:app
# ==============================================================================

import os
import multiprocessing

# Server Socket
bind = os.getenv("BIND_ADDRESS", "127.0.0.1:8050")
backlog = 2048

# Worker Processes
# Single worker: pre-warmed Gemini sessions and caller contexts live in process memory,
# so the dial request and Smartflo's media stream must reach the same process.
workers = int(os.getenv("WEB_CONCURRENCY", "1"))
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
timeout = 120
keepalive = 5

# Process Naming
proc_name = "usd_calling_agent"

# Logging
loglevel = os.getenv("LOG_LEVEL", "info")
accesslog = "-"  # stdout
errorlog = "-"   # stderr

# Graceful Reload & Worker Lifecycle
graceful_timeout = 30
# Disabled: an automatic worker restart would cut every live call on the single worker
max_requests = 0

# Forwarded headers configuration (for reverse proxy behind aaPanel Nginx)
forwarded_allow_ips = "*"
proxy_allow_ips = "*"
