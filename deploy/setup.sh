#!/bin/bash
# One-time EC2 instance setup script.
# Run this once after launching a fresh Ubuntu 22.04 instance.
# Usage: bash setup.sh

set -e

echo "=== Installing system dependencies ==="
sudo apt-get update -y
sudo apt-get install -y python3.11 python3.11-venv python3-pip git nginx certbot python3-certbot-nginx

echo "=== Cloning repository ==="
cd /home/ubuntu
git clone https://github.com/YOUR_GITHUB_USERNAME/backend-ai-run-coach.git app
cd app

echo "=== Creating virtual environment ==="
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "=== Creating .env file ==="
cat > /home/ubuntu/app/.env << 'ENVEOF'
# Paste your .env contents here after running setup.sh
# Or copy it manually: scp .env ubuntu@<EC2_IP>:/home/ubuntu/app/.env
ENVEOF

echo "=== Setting up systemd service ==="
sudo tee /etc/systemd/system/ai-run-coach.service > /dev/null << 'SERVICEEOF'
[Unit]
Description=AI Running Coach Backend
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/app
EnvironmentFile=/home/ubuntu/app/.env
ExecStart=/home/ubuntu/app/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICEEOF

sudo systemctl daemon-reload
sudo systemctl enable ai-run-coach
sudo systemctl start ai-run-coach

echo "=== Configuring nginx ==="
sudo tee /etc/nginx/sites-available/ai-run-coach > /dev/null << 'NGINXEOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE support (for /chat/stream)
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        chunked_transfer_encoding on;
    }
}
NGINXEOF

sudo ln -sf /etc/nginx/sites-available/ai-run-coach /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

echo ""
echo "=== Setup complete ==="
echo "Backend running at http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)"
echo ""
echo "Next steps:"
echo "  1. Copy your .env:  scp .env ubuntu@<EC2_IP>:/home/ubuntu/app/.env"
echo "  2. Restart service: sudo systemctl restart ai-run-coach"
echo "  3. Check logs:      sudo journalctl -u ai-run-coach -f"
