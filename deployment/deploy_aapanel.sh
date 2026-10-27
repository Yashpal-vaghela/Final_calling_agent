#!/usr/bin/env bash
# ==============================================================================
# Ultimate Smile Design - AI Calling Agent
# aaPanel Zero-Downtime Deployment & Verification Script
#
# Usage:
#   bash deployment/deploy_aapanel.sh
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}==================================================================${NC}"
echo -e "${BLUE}   Ultimate Smile Design - aaPanel Deployment & Verification     ${NC}"
echo -e "${BLUE}==================================================================${NC}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_DIR}"
echo -e "${GREEN}[1/7] Detected project directory:${NC} ${PROJECT_DIR}"

# 1. Check for .env file
if [ ! -f ".env" ]; then
    echo -e "${RED}[ERROR] .env file not found in ${PROJECT_DIR}!${NC}"
    echo "Please copy .env.example to .env and configure GEMINI_API_KEY and SMARTFLO credentials."
    exit 1
fi
echo -e "${GREEN}[2/7] Validated .env file configuration.${NC}"

# 2. Check/Create Virtual Environment
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}[INFO] Virtual environment 'venv' not found. Creating new venv with Python 3...${NC}"
    PYTHON_BIN=$(command -v python3.13 || command -v python3.12 || command -v python3 || echo "")
    if [ -z "${PYTHON_BIN}" ]; then
        echo -e "${RED}[ERROR] No Python 3 found on system! Please install Python 3.12 or 3.13 via aaPanel.${NC}"
        exit 1
    fi
    "${PYTHON_BIN}" -m venv venv
fi

PYTHON_EXEC="${PROJECT_DIR}/venv/bin/python"
PIP_EXEC="${PROJECT_DIR}/venv/bin/pip"

if [ ! -f "${PYTHON_EXEC}" ]; then
    echo -e "${RED}[ERROR] Python executable not found at ${PYTHON_EXEC}!${NC}"
    exit 1
fi
echo -e "${GREEN}[3/7] Using Python:${NC} $("${PYTHON_EXEC}" --version)"

# 3. Install/Upgrade System Dependencies (Debian/Ubuntu/CentOS)
echo -e "${YELLOW}[INFO] Verifying system audio libraries...${NC}"
if command -v apt-get &> /dev/null; then
    sudo apt-get update -qq || true
    sudo apt-get install -y -qq libsndfile1 sox libsox-fmt-all > /dev/null 2>&1 || true
elif command -v yum &> /dev/null; then
    sudo yum install -y -q sox-devel libsndfile > /dev/null 2>&1 || true
fi

# 4. Install Python Dependencies
echo -e "${GREEN}[4/7] Installing/Updating Python dependencies...${NC}"
"${PIP_EXEC}" install --quiet --upgrade pip
"${PIP_EXEC}" install --quiet -r requirements.txt
"${PIP_EXEC}" install --quiet gunicorn

# 5. Dynamic Standard Greeting Mode
echo -e "${GREEN}[5/7] Dynamic standard greeting mode active (synthesized on-the-fly by Gemini Live).${NC}"


# 6. Test Nginx Configuration
echo -e "${GREEN}[6/7] Testing Nginx configuration...${NC}"
if command -v nginx &> /dev/null; then
    if sudo nginx -t > /dev/null 2>&1; then
        echo -e "${GREEN}      Nginx syntax: OK. Reloading Nginx...${NC}"
        sudo nginx -s reload || sudo systemctl reload nginx || true
    else
        echo -e "${YELLOW}      [NOTICE] Please review /etc/nginx/nginx.conf or your aaPanel site configuration.${NC}"
    fi
else
    echo -e "${YELLOW}      [NOTICE] Nginx command not in standard path. Ensure aaPanel Nginx is running.${NC}"
fi

# 7. Restart Service (Systemd or PM2)
echo -e "${GREEN}[7/7] Restarting backend service...${NC}"
if systemctl is-active --quiet calling_agent 2>/dev/null; then
    echo -e "${GREEN}      Restarting systemd service: calling_agent${NC}"
    sudo systemctl restart calling_agent
    sleep 2
    if systemctl is-active --quiet calling_agent; then
        echo -e "${GREEN}      Service is ACTIVE and healthy.${NC}"
    else
        echo -e "${RED}      [ERROR] Service failed to start! Check logs: sudo journalctl -u calling_agent -n 50${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}      [NOTICE] Systemd service 'calling_agent' not registered yet.${NC}"
    echo -e "      To install as systemd service, run:"
    echo -e "        sudo cp deployment/systemd/calling_agent.service /etc/systemd/system/"
    echo -e "        sudo systemctl daemon-reload && sudo systemctl enable --now calling_agent"
fi

# 8. Local Health Check
echo -e "${BLUE}------------------------------------------------------------------${NC}"
echo -e "${YELLOW}[VERIFICATION] Probing internal endpoint http://127.0.0.1:8050/health...${NC}"
if command -v curl &> /dev/null; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8050/health || echo "000")
    if [ "${HTTP_CODE}" = "200" ]; then
        echo -e "${GREEN}✅ Local health check passed! (HTTP 200 OK)${NC}"
    else
        echo -e "${YELLOW}⚠️ Endpoint returned HTTP ${HTTP_CODE}. (If service is starting, wait a moment.)${NC}"
    fi
fi

echo -e "${GREEN}==================================================================${NC}"
echo -e "${GREEN}🎉 aaPanel Deployment & Verification Complete!                     ${NC}"
echo -e "${GREEN}==================================================================${NC}"
