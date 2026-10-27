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
# For voice pipelines using in-memory L1 cache + Redis L2 fallback:
# 2 workers deliver optimal CPU usage while preventing excessive RAM duplication.
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
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
max_requests = 5000
max_requests_jitter = 500

# Forwarded headers configuration (for reverse proxy behind aaPanel Nginx)
forwarded_allow_ips = "*"
proxy_allow_ips = "*"
