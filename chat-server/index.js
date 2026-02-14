const { Server } = require("socket.io");
const http = require("http");

// Create a explicit HTTP server to handle the upgrade properly
const server = http.createServer();
const io = new Server(server, {
  path: "/socket.io/", // Explicitly match your Nginx path
  cors: {
    origin: "*",
    methods: ["GET", "POST"]
  },
  perMessageDeflate: false, // Prevents RSV1 bit errors
  allowEIO3: true // Allow older protocol versions if the client is lagging
});

let chatHistory = [];
const MAX_HISTORY = 50;

io.on("connection", (socket) => {
    console.log("User connected:", socket.id);
    setTimeout(() => {
        console.log("Sending history to:", socket.id);
        socket.emit("chat_history", chatHistory);
    }, 1000);

    socket.on("send_message", (data) => {
        console.log("New Message Received:", data.text);
        chatHistory.push(data);
        if (chatHistory.length > MAX_HISTORY) chatHistory.shift();
        io.emit("receive_message", data);
    });

    socket.on("disconnect", () => {
        console.log("User disconnected:", socket.id);
    });
});

// Bind to 0.0.0.0 for Docker compatibility
server.listen(3000, "0.0.0.0", () => {
    console.log("--- Chat Server LIVE on Port 3000 ---");
});
