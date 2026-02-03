from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
import os
import json
import uuid
import threading
import time
from datetime import datetime
import base64

app = Flask(__name__)
app.config["SECRET_KEY"] = "secure-messenger-secret-key-2024"
app.config["UPLOAD_FOLDER"] = "static/uploads"
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Настройки
CHATS_FILE = "data/chats.json"
USERS_FILE = "data/users.json"
os.makedirs("data", exist_ok=True)
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# Хранилище в памяти
active_users = {}
camera_streams = {}
chat_messages = {}
connected_clients = {}

# Загрузка данных
def load_data(filename, default=[]):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except:
        return default

def save_data(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

# ===== API ДЛЯ FLUTTER =====
@app.route("/api/status")
def api_status():
    active_cameras = len([c for c in camera_streams if time.time() - camera_streams[c].get("last_active", 0) < 30])
    return jsonify({
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "statistics": {
            "active_users": len(active_users),
            "active_cameras": active_cameras,
            "active_chats": len(chat_messages),
            "total_messages": sum(len(msgs) for msgs in chat_messages.values())
        }
    })

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.json
    user_id = str(uuid.uuid4())
    username = data.get("username", f"User_{user_id[:8]}")
    
    active_users[user_id] = {
        "username": username,
        "login_time": datetime.now().isoformat(),
        "last_seen": time.time()
    }
    
    return jsonify({
        "user_id": user_id,
        "username": username,
        "token": str(uuid.uuid4())
    })

@app.route("/api/chats")
def api_chats():
    chats = []
    for room_id, messages in chat_messages.items():
        if messages:
            last_message = messages[-1]
            chats.append({
                "room_id": room_id,
                "users": list(connected_clients.get(room_id, [])),
                "last_message": last_message["text"],
                "timestamp": last_message["timestamp"],
                "message_count": len(messages)
            })
    return jsonify({"chats": chats})

@app.route("/api/camera/status")
def camera_status_api():
    cameras = {}
    for camera_id, data in camera_streams.items():
        cameras[camera_id] = {
            "start_time": data.get("start_time"),
            "last_active": data.get("last_active"),
            "fps": data.get("fps", 0),
            "status": "active" if time.time() - data.get("last_active", 0) < 30 else "inactive",
            "user_id": data.get("user_id", "unknown")
        }
    return jsonify({
        "active_cameras": len([c for c in cameras if cameras[c]["status"] == "active"]),
        "cameras": cameras
    })

# ===== WEBSOCKET ДЛЯ ЧАТА =====
@socketio.on("connect")
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on("disconnect")
def handle_disconnect():
    for room_id in list(connected_clients.keys()):
        connected_clients[room_id].discard(request.sid)
        if not connected_clients[room_id]:
            del connected_clients[room_id]
    print(f"Client disconnected: {request.sid}")

@socketio.on("join_chat")
def handle_join_chat(data):
    room_id = data.get("room_id", "general")
    user_id = data.get("user_id")
    username = data.get("username", "Anonymous")
    
    join_room(room_id)
    connected_clients[room_id] = connected_clients.get(room_id, set())
    connected_clients[room_id].add(request.sid)
    
    active_users[user_id] = {
        "username": username,
        "room_id": room_id,
        "last_seen": time.time()
    }
    
    if room_id in chat_messages:
        emit("chat_history", {"messages": chat_messages[room_id][-50:]})
    
    emit("user_joined", {
        "user_id": user_id,
        "username": username,
        "timestamp": datetime.now().isoformat()
    }, room=room_id, skip_sid=request.sid)

@socketio.on("send_message")
def handle_send_message(data):
    room_id = data.get("room_id", "general")
    message_data = {
        "id": str(uuid.uuid4()),
        "user_id": data.get("user_id"),
        "username": data.get("username", "Anonymous"),
        "text": data.get("text", ""),
        "timestamp": datetime.now().isoformat(),
        "type": "text"
    }
    
    if room_id not in chat_messages:
        chat_messages[room_id] = []
    
    chat_messages[room_id].append(message_data)
    
    if len(chat_messages[room_id]) > 1000:
        chat_messages[room_id] = chat_messages[room_id][-1000:]
    
    emit("new_message", message_data, room=room_id)

@socketio.on("camera_stream")
def handle_camera_stream(data):
    camera_id = data.get("camera_id", str(uuid.uuid4()))
    frame_data = data.get("frame")
    
    if not frame_data:
        return
    
    try:
        camera_streams[camera_id] = {
            "camera_id": camera_id,
            "user_id": data.get("user_id"),
            "last_active": time.time(),
            "fps": camera_streams.get(camera_id, {}).get("fps", 0) + 1,
            "start_time": camera_streams.get(camera_id, {}).get("start_time", time.time())
        }
        
        emit("camera_ack", {
            "camera_id": camera_id,
            "status": "received",
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        print(f"Error handling camera stream: {e}")

# ===== АДМИН ПАНЕЛЬ =====
@app.route("/admin")
def admin_login_page():
    return '''<!DOCTYPE html>
<html>
<head>
    <title>Admin Login</title>
    <style>
        body { font-family: Arial; padding: 50px; }
        .login-box { max-width: 300px; margin: 0 auto; }
        input, button { width: 100%; padding: 10px; margin: 5px 0; }
    </style>
</head>
<body>
    <div class="login-box">
        <h2>Admin Login</h2>
        <input type="text" placeholder="Username" id="username" value="admin">
        <input type="password" placeholder="Password" id="password" value="admin123">
        <button onclick="login()">Login</button>
    </div>
    <script>
        function login() {
            if(document.getElementById("username").value === "admin" &&
               document.getElementById("password").value === "admin123") {
                window.location.href = "/admin/dashboard";
            } else {
                alert("Invalid credentials");
            }
        }
    </script>
</body>
</html>'''

@app.route("/admin/dashboard")
def admin_dashboard():
    return '''<!DOCTYPE html>
    <html>
    <head>
        <title>Admin Dashboard</title>
        <style>
            body { font-family: Arial; padding: 20px; }
            .stats { display: flex; gap: 20px; margin-bottom: 30px; }
            .stat-card { padding: 20px; background: #f5f5f5; border-radius: 10px; }
            button { padding: 10px 20px; margin: 5px; }
        </style>
    </head>
    <body>
        <h1>Admin Dashboard</h1>
        <div class="stats">
            <div class="stat-card">
                <h3 id="users">0</h3>
                <p>Active Users</p>
            </div>
            <div class="stat-card">
                <h3 id="cameras">0</h3>
                <p>Active Cameras</p>
            </div>
        </div>
        <button onclick="refresh()">Refresh</button>
        <script>
            async function refresh() {
                const res = await fetch("/api/status");
                const data = await res.json();
                document.getElementById("users").textContent = data.statistics.active_users;
                document.getElementById("cameras").textContent = data.statistics.active_cameras;
            }
            refresh();
        </script>
    </body>
    </html>'''

@app.route("/")
def index():
    return jsonify({
        "app": "Secure Messenger Backend",
        "version": "1.0.0",
        "endpoints": {
            "admin": "/admin",
            "api_status": "/api/status",
            "api_login": "/api/auth/login (POST)"
        }
    })

# Фоновая задача для очистки
def cleanup_task():
    while True:
        time.sleep(60)
        current_time = time.time()
        
        # Очистка неактивных пользователей
        inactive_users = []
        for user_id, info in list(active_users.items()):
            if current_time - info.get("last_seen", 0) > 300:
                inactive_users.append(user_id)
        
        for user_id in inactive_users:
            del active_users[user_id]
        
        # Очистка неактивных камер
        inactive_cameras = []
        for camera_id, data in list(camera_streams.items()):
            if current_time - data.get("last_active", 0) > 600:
                inactive_cameras.append(camera_id)
        
        for camera_id in inactive_cameras:
            del camera_streams[camera_id]

# Запускаем фоновую задачу
cleanup_thread = threading.Thread(target=cleanup_task)
cleanup_thread.daemon = True
cleanup_thread.start()

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Secure Messenger Server Starting...")
    print("=" * 60)
    print("📊 Admin panel: http://127.0.0.1:5000/admin")
    print("   Username: admin")
    print("   Password: admin123")
    print("📱 API: http://127.0.0.1:5000/")
    print("=" * 60)
    
    # Измени эту строку - добавь allow_unsafe_werkzeug=True
    socketio.run(app, debug=False, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
