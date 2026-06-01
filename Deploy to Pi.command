#!/bin/bash
cd "$(dirname "$0")"
echo "Deploying to Pi..."
scp index.html server.py codydoerfler@100.80.87.42:~/bitaxe/
ssh codydoerfler@100.80.87.42 "sudo systemctl restart bitaxe-dashboard"
echo "Done! http://100.80.87.42:3000"
