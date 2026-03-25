#!/bin/bash
# Server Deployment Script for Timestamp Fix

echo "================================================"
echo "  Deploying Chat Server Timestamp Fix"
echo "================================================"
echo ""

cd /home/vitowiratara/QuakeAlert-Server/chat-server || exit 1

echo "✅ Step 1: Stopping old server..."
if command -v pm2 &> /dev/null; then
    pm2 stop chat-server 2>/dev/null || echo "  (Server not running via PM2)"
elif docker ps | grep -q chat-server; then
    docker-compose stop chat-server
    echo "  Stopped Docker container"
else
    pkill -f "node index.js" || echo "  (No running node process found)"
fi
echo ""

echo "✅ Step 2: Backup current code..."
cp index.js index.js.backup.$(date +%Y%m%d_%H%M%S)
echo "  Backup created"
echo ""

echo "✅ Step 3: Server code already updated!"
echo "  Changed: timestamp from seconds to milliseconds"
echo "  Line 86: timestamp: Date.now()"
echo ""

echo "✅ Step 4: Starting server..."
if command -v pm2 &> /dev/null; then
    pm2 start index.js --name chat-server
    pm2 save
    echo "  Started with PM2"
elif [ -f "docker-compose.yml" ]; then
    docker-compose up -d chat-server
    echo "  Started with Docker"
else
    nohup node index.js > chat-server.log 2>&1 &
    echo "  Started in background (PID: $!)"
fi
echo ""

echo "✅ Step 5: Verifying server..."
sleep 2
if command -v pm2 &> /dev/null; then
    pm2 status chat-server
elif docker ps | grep -q chat-server; then
    docker ps | grep chat-server
else
    if pgrep -f "node index.js" > /dev/null; then
        echo "  ✅ Server is running!"
    else
        echo "  ❌ Server failed to start - check logs"
        exit 1
    fi
fi
echo ""

echo "================================================"
echo "  ✅ Deployment Complete!"
echo "================================================"
echo ""
echo "Next steps:"
echo "1. Test: Send a message from the app"
echo "2. Check logs: tail -f chat-server.log"
echo "   (or: pm2 logs chat-server)"
echo "3. Verify timestamp has 13 digits (milliseconds)"
echo ""
echo "To rollback:"
echo "  cp index.js.backup.* index.js"
echo "  pm2 restart chat-server"
echo ""
