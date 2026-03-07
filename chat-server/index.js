const { Server } = require("socket.io");
const http = require("http");
const fs = require("fs");

// Create a explicit HTTP server to handle the upgrade properly
const server = http.createServer();
const CORS_ORIGIN = process.env.CORS_ORIGIN || "https://quakealert.bananapixel.my.id";
const io = new Server(server, {
  path: "/socket.io/", // Explicitly match your Nginx path
  cors: {
    origin: CORS_ORIGIN,
    methods: ["GET", "POST"]
  },
  perMessageDeflate: false, // Prevents RSV1 bit errors
  allowEIO3: true // Allow older protocol versions if the client is lagging
});

let chatHistory = [];
const MAX_HISTORY = 50;

// OPTIONAL: Basic Bad Word Filter
let BAD_WORDS = [];
try {
    if (fs.existsSync('./badwords.json')) {
        const rawData = fs.readFileSync('./badwords.json');
        BAD_WORDS = JSON.parse(rawData);
        console.log(`Loaded ${BAD_WORDS.length} bad words from file.`);
    } else {
        console.log("Warning: badwords.json not found. Filter disabled.");
    }
} catch (error) {
    console.error("Error loading bad words:", error);
}

io.on("connection", (socket) => {
    console.log("User connected:", socket.id);

    // 1. Initialize spam tracker for this user
    socket.lastMessageTime = 0;
    
    // Broadcast updated online count to all clients
    io.emit("online_count", io.engine.clientsCount);
    
    setTimeout(() => {
        console.log("Sending history to:", socket.id);
        socket.emit("chat_history", chatHistory);
    }, 1000);

    socket.on("send_message", (data) => {
        const now = Date.now();
        // Safety: Ensure message exists and is a string
        const text = (data.message || "").toString();

        // --- SECURITY CHECK 1: RATE LIMITING ---
        // User must wait 3 second (3000ms) between messages
        if (now - socket.lastMessageTime < 3000) {
            console.log(`Spam rejected from ${socket.id}`);
            return; // Ignore the message
        }

        // --- SECURITY CHECK 2: MAX LENGTH ---
        // Prevent huge messages that crash phones (Limit: 500 chars)
        if (text.length > 500) {
            console.log(`Message too long from ${socket.id}`);
            socket.emit("error_message", "Message too long! Limit is 500 characters.");
            return;
        }

        // --- SECURITY CHECK 3: EMPTY MESSAGES ---
        // Don't allow blank messages or just spaces
        if (text.trim().length === 0) {
            return;
        }

        // --- SECURITY CHECK 4: PROFANITY FILTER (Optional) ---
        // Check if message contains bad words
        const lowerCaseText = text.toLowerCase();
        for (const word of BAD_WORDS) {
            if (lowerCaseText.includes(word)) {
                console.log(`Profanity blocked from ${socket.id}`);
                return; // Silent block (Shadow ban style)
            }
        }

        // --- ALL CHECKS PASSED ---
        socket.lastMessageTime = now; // Update timer

        // Create the message object with server-side timestamp
        // ✅ FIXED: Use milliseconds (not seconds) for consistency with Android
        const messageWithTime = {
            senderId: data.senderId,
            message: text, // Use the sanitized text
            timestamp: Date.now()  // Milliseconds (consistent with Android/iOS)
        };

        console.log("New Message Received:", messageWithTime.message, "at", messageWithTime.timestamp);
        
        chatHistory.push(messageWithTime);
        if (chatHistory.length > MAX_HISTORY) chatHistory.shift();
        
        io.emit("receive_message", messageWithTime);
    });

    socket.on("disconnect", () => {
        console.log("User disconnected:", socket.id);
        // Broadcast updated online count to all remaining clients
        io.emit("online_count", io.engine.clientsCount);
    });
});

// Bind to 0.0.0.0 for Docker compatibility
server.listen(3000, "0.0.0.0", () => {
    console.log("--- Chat Server LIVE on Port 3000 ---");
});
