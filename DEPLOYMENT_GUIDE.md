# 🚀 Quick Deployment Guide - Timestamp Fix

## Changes Made

### ✅ Server Changes (1 file)
**File:** `QuakeAlert-Server/chat-server/index.js`  
**Change:** Line 86
```javascript
// Before: timestamp: Math.floor(Date.now() / 1000)  ❌ seconds
// After:  timestamp: Date.now()                     ✅ milliseconds
```

### ✅ Client Changes (1 file)
**File:** `app/src/main/java/.../TelegramChatFragment.kt`  
**Changes:**
1. Removed timestamp conversion in `receive_message` (line 143-165)
2. Removed timestamp conversion in `chat_history` (line 167-195)
3. Added old message auto-fix (line 203-238)

---

## 🔄 Deployment Steps

### Step 1: Deploy Server (Required First!)

```bash
cd /home/vitowiratara/QuakeAlert-Server/chat-server

# Option A: Using the deploy script
./deploy-timestamp-fix.sh

# Option B: Manual deployment
pm2 stop chat-server
node index.js  # Test it works
pm2 start index.js --name chat-server
pm2 save
```

**Verify server is running:**
```bash
pm2 logs chat-server --lines 20
# Should see: "--- Chat Server LIVE on Port 3000 ---"
```

---

### Step 2: Build & Install App

```bash
cd /home/vitowiratara/QuakeAlert-App-Android

# Build the APK
./gradlew assembleFdroidDebug

# Install to connected device
./gradlew installFdroidDebug

# Or manually
adb install -r app/build/outputs/apk/fdroid/debug/app-fdroid-debug.apk
```

---

### Step 3: Test Everything

#### Test 1: Clear Old Data (Optional but Recommended)
```bash
# Clear app data to start fresh
adb shell pm clear id.my.bananapixel.quakealert.debug
```

#### Test 2: Send Message
1. Open app → Navigate to Chat
2. Type "Test message"
3. Press send

**Expected:**
- ✅ Message appears immediately
- ✅ Shows current time (e.g., "14:30")
- ✅ Date header shows "Today"

#### Test 3: Check Server Logs
```bash
pm2 logs chat-server --lines 5
```

**Expected output:**
```
New Message Received: Test message at 1709846400000
                                          ↑
                                    13 digits = milliseconds ✅
```

#### Test 4: Cross-Device Sync
1. Install app on Device B
2. Send from Device A
3. Check Device B receives it

**Expected:**
- ✅ Both show same timestamp
- ✅ Both show same date header
- ✅ Times are synchronized

#### Test 5: Chat History
1. Close and reopen app
2. **Expected:** All messages load with correct times

---

## 🐛 Troubleshooting

### Issue: Dates still wrong

**Check server timestamp format:**
```bash
pm2 logs chat-server | grep "New Message"
# Timestamp should have 13 digits (milliseconds)
# If 10 digits → Server not updated, redeploy
```

**Clear app cache:**
```bash
adb shell pm clear id.my.bananapixel.quakealert.debug
# Reinstall app
./gradlew installFdroidDebug
```

### Issue: Old messages show year 1970

**This is the old data issue - the fix handles it automatically:**
- Old messages (seconds format) are converted on display
- If still seeing 1970, check the conversion logic at line 207-212

**Manual fix (if needed):**
```bash
# Clear database and reload from server
adb shell pm clear id.my.bananapixel.quakealert.debug
```

### Issue: Server not starting

**Check port 3000 is available:**
```bash
lsof -i :3000
# If something is using it:
kill -9 <PID>
```

**Check Node.js version:**
```bash
node --version
# Should be v14 or higher
```

**Check for errors:**
```bash
cd /home/vitowiratara/QuakeAlert-Server/chat-server
node index.js
# Run directly to see errors
```

---

## 📊 Verification Commands

### Check Server Status
```bash
pm2 status
# Should show: chat-server | online
```

### Monitor Real-Time
```bash
# Server logs
pm2 logs chat-server

# Client logs
adb logcat -s "TelegramChatFragment:*" "SaveChatMessagesUseCase:*"
```

### Test Timestamp Format
```bash
# Send test message from app, then:
pm2 logs chat-server --lines 1
# Look for: "at 1709846400000" (13 digits)
```

---

## 🎯 Quick Checklist

### Server Deployment
- [ ] Server code updated (index.js line 86)
- [ ] Server restarted successfully
- [ ] Server logs show timestamps with 13 digits
- [ ] Port 3000 is accessible

### Client Deployment
- [ ] App built successfully
- [ ] App installed on device
- [ ] Old data cleared (optional)
- [ ] Socket connection works

### Testing
- [ ] New messages send correctly
- [ ] Timestamps show current time
- [ ] Date headers accurate ("Today", etc.)
- [ ] Cross-device sync works
- [ ] Chat history loads properly
- [ ] Old messages display correctly

---

## 📝 Files Changed Summary

```
QuakeAlert-Server/
└── chat-server/
    ├── index.js                      ✅ Modified (1 line)
    └── deploy-timestamp-fix.sh       ✅ Created (new)

QuakeAlert-App-Android/
└── app/src/main/java/.../
    └── TelegramChatFragment.kt       ✅ Modified (40 lines)
```

---

## 🔄 Rollback Plan

### If Something Goes Wrong

**Rollback Server:**
```bash
cd /home/vitowiratara/QuakeAlert-Server/chat-server
# Find backup
ls -lt index.js.backup.*
# Restore
cp index.js.backup.YYYYMMDD_HHMMSS index.js
pm2 restart chat-server
```

**Rollback Client:**
```bash
cd /home/vitowiratara/QuakeAlert-App-Android
git checkout HEAD -- app/src/main/java/id/my/bananapixel/quakealert/ui/TelegramChatFragment.kt
./gradlew installFdroidDebug
```

---

## ✅ Success Indicators

**You'll know it's working when:**
1. ✅ Messages send instantly
2. ✅ Timestamps show as "HH:mm" format
3. ✅ Date headers group correctly
4. ✅ Server logs show 13-digit timestamps
5. ✅ Cross-device sync is instant
6. ✅ No duplicated messages
7. ✅ Old messages display correct dates

---

**Status:** Ready to Deploy  
**Estimated Deploy Time:** 5 minutes  
**Downtime:** ~30 seconds (server restart)  
**Risk Level:** Low (changes are backward compatible)
