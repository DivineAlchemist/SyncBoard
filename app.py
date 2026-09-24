from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "mysync.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = "mysync-local-v2"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clipboards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            device_name TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def get_clipboards(limit=500):
    conn = db()
    rows = conn.execute(
        """SELECT id, device_id, device_name, content, created_at
           FROM clipboards
           ORDER BY id DESC LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/clipboards")
def api_clipboards():
    return jsonify(get_clipboards())


@app.post("/api/clipboards")
def add_clipboard():
    data = request.get_json(silent=True) or {}

    content = data.get("content", "")
    device_id = (data.get("device_id") or "").strip()
    device_name = (data.get("device_name") or "").strip()

    if not device_id:
        return jsonify({"error": "A device ID is required."}), 400

    if not device_name:
        device_name = "Unnamed device"

    if not isinstance(content, str) or not content.strip():
        return jsonify({"error": "Clipboard content cannot be empty."}), 400

    if len(content) > 1_000_000:
        return jsonify({"error": "Clipboard is too large (1 MB maximum)."}), 413

    created_at = datetime.now(timezone.utc).isoformat()

    conn = db()
    cur = conn.execute(
        """INSERT INTO clipboards
           (device_id, device_name, content, created_at)
           VALUES (?, ?, ?, ?)""",
        (device_id, device_name, content, created_at),
    )
    item_id = cur.lastrowid
    conn.commit()
    conn.close()

    item = {
        "id": item_id,
        "device_id": device_id,
        "device_name": device_name,
        "content": content,
        "created_at": created_at,
    }

    socketio.emit("clipboard_added", item)
    return jsonify(item), 201


@app.delete("/api/clipboards/<int:item_id>")
def delete_clipboard(item_id):
    conn = db()
    cur = conn.execute("DELETE FROM clipboards WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        return jsonify({"error": "Clipboard item not found."}), 404

    socketio.emit("clipboard_deleted", {"id": item_id})
    return jsonify({"ok": True})


@socketio.on("connect")
def connected():
    emit("clipboard_snapshot", get_clipboards())


if __name__ == "__main__":
    init_db()
    print("MySync running on http://0.0.0.0:8000")
    socketio.run(app, host="0.0.0.0", port=8000, debug=False)
