#!/bin/bash
# Deploy latest code to EC2.
# Run this from your local machine whenever you push updates.
# Usage: bash deploy/deploy.sh

set -e

EC2_IP="${EC2_IP:-13.54.81.73}"
SSH_KEY="${SSH_KEY:-~/.ssh/ai-run-coach.pem}"

echo "=== Deploying to $EC2_IP ==="

ssh -i "$SSH_KEY" ubuntu@"$EC2_IP" << 'EOF'
  cd /home/ubuntu/app
  git pull origin main
  .venv/bin/pip install -r requirements.txt
  sudo systemctl restart ai-run-coach
  echo "Deploy complete. Service restarted."
EOF

echo "=== Done — backend live at http://$EC2_IP ==="
