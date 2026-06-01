#!/bin/bash
cd "$(dirname "$0")"
python3 server.py &
sleep 1
open http://localhost:3000
wait
