#!/bin/bash
# LEGACY — from when the dashboard ran on the Raspberry Pi. The dashboard now
# runs on the Mac mini, where "deploy" is just: git pull the branch, then
# "Restart Dashboard.command" (no scp). Kept for reference only.
cd "$(dirname "$0")"
echo "Deploying to Pi..."
scp index.html server.py codydoerfler@100.80.87.42:~/bitaxe/
ssh codydoerfler@100.80.87.42 "sudo systemctl restart bitaxe-dashboard"
echo "Done! http://100.80.87.42:3000"
