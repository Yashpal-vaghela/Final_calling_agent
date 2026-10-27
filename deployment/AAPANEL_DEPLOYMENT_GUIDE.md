# aaPanel Production Deployment & Nginx Zero-Latency Optimization Guide

This guide covers deploying the **Ultimate Smile Design (Kiara) AI Voice Calling Agent** on an **aaPanel** Linux server (Ubuntu, Debian, CentOS, AlmaLinux, or Rocky Linux).

---

## 1. System Architecture on aaPanel

```mermaid
sequenceDiagram
    autonumber
    participant Caller as Caller Mobile
    participant Smartflo as Tata Smartflo Telephony
    participant Nginx as aaPanel Nginx (Port 443 / SSL)
    participant FastAPI as FastAPI / Gunicorn (127.0.0.1:8050)
    participant Gemini as Google Gemini Live (WSS)

    Caller->>Smartflo: Inbound Call / Outbound Answer
    Smartflo->>Nginx: POST https://your-domain.com/smartflo/voice
    Nginx->>FastAPI: Forward Webhook (ProxyHeadersMiddleware)
    FastAPI-->>Nginx: 200 OK {"wss_url": "wss://your-domain.com/smartflo/media-stream"}
    Nginx-->>Smartflo: Deliver WSS URL
    Smartflo->>Nginx: WebSocket Handshake (/smartflo/media-stream)
    Nginx->>FastAPI: Unbuffered TCP Stream (tcp_nodelay on; proxy_buffering off)
    FastAPI-->>Caller: 🚀 Instant Greeting (50ms local RAM queue -> 1.2s to phone)
    FastAPI->>Gemini: Background WSS Connect + Silent Prefill
    Note over Caller,Gemini: Interactive AI Conversation Begins
```

---

## 2. Prerequisites in aaPanel

1. **aaPanel Installed**: Running Nginx 1.22+ or 1.24+.
2. **Python Version**: Python **3.12** or **3.13** (Install via aaPanel *App Store* -> *Python Project Manager* or system package manager).
3. **Domain Name**: Pointed via DNS `A` record to your aaPanel server IP (e.g. `call.ultimatesmiledesign.com`).
4. **Ports**: Port `80` and `443` open in aaPanel firewall and Cloud security group (AWS, GCP, DigitalOcean, or Hostinger).

---

## 3. Step-by-Step Deployment

### Step 1: Upload Project Code to aaPanel

In aaPanel:
1. Navigate to **Files** -> Go to `/www/wwwroot/`.
2. Create or open the directory for your domain, e.g. `/www/wwwroot/call.ultimatesmiledesign.com`.
3. Upload or git-clone the project repository into this folder:
   ```bash
   cd /www/wwwroot/call.ultimatesmiledesign.com
   git pull origin main  # or upload files
   ```

---

### Step 2: Configure Environment Variables (`.env`)

Inside the project directory on your server:
1. Create or edit `.env`:
   ```bash
   nano /www/wwwroot/call.ultimatesmiledesign.com/.env
   ```
2. Ensure the following production values are configured:
   ```ini
   # Google Gemini API
   GEMINI_API_KEY=your_gemini_api_key_here
   GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview
   GEMINI_LIVE_VOICE=Aoede

   # Tata Smartflo Configuration
   SMARTFLO_APP_HOST=call.ultimatesmiledesign.com
   SMARTFLO_API_KEY=your_smartflo_api_key
   SMARTFLO_BEARER_TOKEN=your_smartflo_jwt_bearer_token
   SMARTFLO_BASE_URL=https://api-smartflo.tatateleservices.com
   SMARTFLO_CALLER_ID=917965262987
   SMARTFLO_AGENT_NUMBER=1001

   # Jitter Buffer & Audio Pipeline
   ENABLE_NOISE_GATE=true
   PREBUFFER_THRESHOLD_BYTES=640
   AUDIO_RESAMPLE_ENGINE=soxr

   # Redis Configuration (Optional Upstash Cloud L2)
   REDIS_ENABLED=true
   REDIS_URL=rediss://default:your_token@your-endpoint.upstash.io:6379
   ```

---

### Step 3: Run the Automated Deployment Script

Run the automated script included in this repository:
```bash
cd /www/wwwroot/call.ultimatesmiledesign.com
chmod +x deployment/deploy_aapanel.sh
sudo bash deployment/deploy_aapanel.sh
```
The script automatically:
* Installs system audio codecs (`libsndfile1`, `sox`).
* Sets up the Python virtual environment (`venv`).
* Installs dependencies from `requirements.txt`.
* Pre-loads the studio-grade 8kHz μ-law greeting into RAM.
* Verifies Nginx and restarts the services.

---

### Step 4: Configure aaPanel Nginx Reverse Proxy (Critical Zero-Latency Settings)

In default aaPanel reverse proxy configurations, Nginx enables `proxy_buffering on;`. This collects 4KB of audio packets before sending, adding **500ms–1000ms** of unwanted audio delay and causing choppy speech.

#### How to apply in aaPanel:
1. In aaPanel, go to **Website** -> Click on your site (`call.ultimatesmiledesign.com`).
2. Click on the **Config** tab (or **Reverse Proxy** -> Edit config).
3. Add the zero-latency audio block inside your `server { ... }` configuration:

```nginx
# ==============================================================================
# ZERO-LATENCY WEBSOCKET PROXY (TATA SMARTFLO MEDIA STREAM)
# ==============================================================================
location /smartflo/media-stream {
    proxy_pass http://127.0.0.1:8050;
    proxy_http_version 1.1;

    # WebSocket Upgrade
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    # Identity Forwarding
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Port $server_port;

    # CRITICAL: Disable Buffering for Instant Audio Transmission
    proxy_buffering off;
    proxy_cache off;
    proxy_request_buffering off;
    tcp_nodelay on;
    tcp_nopush off;

    # Extended Call Timeouts (1 Hour)
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_connect_timeout 15s;
}

# ==============================================================================
# STANDARD HTTP API & FORM ENDPOINTS
# ==============================================================================
location / {
    proxy_pass http://127.0.0.1:8050;
    proxy_http_version 1.1;

    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;

    # Buffer tuning for fast JSON API responses
    proxy_buffer_size 128k;
    proxy_buffers 4 256k;
    proxy_busy_buffers_size 256k;

    client_max_body_size 50M;
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;
}
```
4. Click **Save** in aaPanel. Nginx will automatically validate syntax and reload.

---

### Step 5: Configure SSL (Let's Encrypt in aaPanel)

1. In aaPanel, go to **Website** -> Click on your domain.
2. Click the **SSL** tab.
3. Select **Let's Encrypt** -> Check your domain name.
4. Click **Apply**.
5. Once issued, enable **Force HTTPS**.

---

### Step 6: Register Systemd Service for High Availability

To ensure the calling agent runs 24/7 and restarts automatically if the server reboots:

1. Copy the systemd service file:
   ```bash
   sudo cp deployment/systemd/calling_agent.service /etc/systemd/system/
   ```
2. Edit `/etc/systemd/system/calling_agent.service` to make sure `WorkingDirectory` matches your path:
   ```ini
   WorkingDirectory=/www/wwwroot/call.ultimatesmiledesign.com
   Environment="PATH=/www/wwwroot/call.ultimatesmiledesign.com/venv/bin:/usr/local/bin:/usr/bin:/bin"
   ```
3. Reload systemd and enable service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable calling_agent
   sudo systemctl restart calling_agent
   sudo systemctl status calling_agent
   ```

---

## 4. Verification & Testing

### 1. Internal Health Check
Run on your aaPanel terminal:
```bash
curl -i http://127.0.0.1:8050/health
```
**Expected Response:**
```http
HTTP/1.1 200 OK
content-type: application/json
{"status":"ok"}
```

### 2. Public HTTPS & Form Portal Check
Visit `https://your-domain.com/contact-form` or `https://your-domain.com/booking-form` in your browser.
The page should load with SSL lock, instant city lookup, and active styling.

### 3. Smartflo Webhook URL Verification
In your **Tata Smartflo Portal**:
* **Inbound Voice Webhook URL:** `https://your-domain.com/smartflo/voice`
* **HTTP Method:** `POST`

---

## 5. Production Maintenance & Logs

* **View Real-Time Agent Logs:**
  ```bash
  sudo journalctl -u calling_agent -f
  ```
* **View Nginx Access & Error Logs:**
  ```bash
  tail -f /www/wwwlogs/your-domain.com.log
  tail -f /www/wwwlogs/your-domain.com.error.log
  ```
* **Restart the Agent Service:**
  ```bash
  sudo systemctl restart calling_agent
  ```
