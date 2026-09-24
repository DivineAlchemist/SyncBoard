import hmac
import os
import re
import secrets
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    # Safe for local development. Render gets a generated SECRET_KEY from render.yaml.
    secret_key = "dev-" + secrets.token_hex(32)

app.config.update(
    SECRET_KEY=secret_key,
    SQLALCHEMY_DATABASE_URI=(
        os.getenv("DATABASE_URL")
        or f"sqlite:///{(INSTANCE_DIR / 'mysync.db').as_posix()}"
    ),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    MAX_CONTENT_LENGTH=1_200_000,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
)

# SQLAlchemy needs the psycopg driver for PostgreSQL. Render can provide either
# postgresql:// or the older postgres:// form, so normalize both here.
database_url = app.config["SQLALCHEMY_DATABASE_URI"]
if database_url.startswith("postgres://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgres://") :]
elif database_url.startswith("postgresql://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgresql://") :]
app.config["SQLALCHEMY_DATABASE_URI"] = database_url

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = None
socketio = SocketIO(app, async_mode="threading")


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(24), unique=True, nullable=False, index=True)
    email = db.Column(db.String(320), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(60), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    clips = db.relationship(
        "ClipboardItem",
        back_populates="owner",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    contacts = db.relationship(
        "Contact",
        foreign_keys="Contact.viewer_id",
        back_populates="viewer",
        cascade="all, delete-orphan",
    )


class Contact(db.Model):
    __tablename__ = "contacts"
    __table_args__ = (db.UniqueConstraint("viewer_id", "owner_id", name="uq_contact_viewer_owner"),)

    id = db.Column(db.Integer, primary_key=True)
    viewer_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    viewer = db.relationship("User", foreign_keys=[viewer_id], back_populates="contacts")
    owner = db.relationship("User", foreign_keys=[owner_id])


class ClipboardItem(db.Model):
    __tablename__ = "clipboard_items"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id = db.Column(db.String(100), nullable=False, index=True)
    device_name = db.Column(db.String(80), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_shared = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    owner = db.relationship("User", back_populates="clips")


with app.app_context():
    db.create_all()


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def make_public_id() -> str:
    while True:
        candidate = "MS-" + secrets.token_hex(6).upper()
        if db.session.execute(select(User.id).where(User.public_id == candidate)).scalar_one_or_none() is None:
            return candidate


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def inject_template_helpers():
    return {"csrf_token": csrf_token}


@app.before_request
def protect_state_changing_requests():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return

    sent = request.headers.get("X-CSRF-Token")
    if not sent:
        sent = request.form.get("csrf_token")

    expected = session.get("csrf_token")
    if not sent or not expected or not hmac.compare_digest(sent, expected):
        abort(400, description="Invalid CSRF token.")


def json_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "You must be logged in."}), 401
        return view(*args, **kwargs)

    return wrapped


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def serialize_user(user: User, include_email: bool = False):
    data = {
        "public_id": user.public_id,
        "display_name": user.display_name,
    }
    if include_email:
        data["email"] = user.email
    return data


def serialize_clip(item: ClipboardItem):
    return {
        "id": item.id,
        "content": item.content,
        "device_id": item.device_id,
        "device_name": item.device_name,
        "is_shared": item.is_shared,
        "created_at": utc_iso(item.created_at),
        "owner": serialize_user(item.owner),
        "is_mine": item.user_id == current_user.id,
    }


def visible_items_for(user: User):
    followed_owner_ids = [contact.owner_id for contact in user.contacts]
    conditions = [ClipboardItem.user_id == user.id]
    if followed_owner_ids:
        conditions.append(
            (ClipboardItem.user_id.in_(followed_owner_ids)) & (ClipboardItem.is_shared.is_(True))
        )

    query = (
        select(ClipboardItem)
        .options(joinedload(ClipboardItem.owner))
        .where(or_(*conditions))
        .order_by(ClipboardItem.created_at.desc(), ClipboardItem.id.desc())
        .limit(1000)
    )
    return db.session.execute(query).scalars().unique().all()


@app.get("/healthz")
def healthz():
    try:
        db.session.execute(select(1))
        return jsonify({"status": "ok"})
    except Exception:
        app.logger.exception("Health check database failure")
        return jsonify({"status": "error"}), 503


@app.get("/")
def index():
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    return render_template("index.html", user=serialize_user(current_user, include_email=True))


@app.get("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.post("/login")
def login_post():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    user = db.session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user or not check_password_hash(user.password_hash, password):
        return render_template("login.html", error="Invalid email or password."), 401

    session.clear()
    login_user(user, remember=True)
    csrf_token()
    return redirect(url_for("index"))


@app.get("/signup")
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("signup.html")


@app.post("/signup")
def signup_post():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    display_name = request.form.get("display_name", "").strip()

    if not EMAIL_RE.fullmatch(email) or len(email) > 320:
        return render_template("signup.html", error="Enter a valid email address."), 400
    if not 8 <= len(password) <= 128:
        return render_template("signup.html", error="Password must be 8–128 characters."), 400
    if not 1 <= len(display_name) <= 60:
        return render_template("signup.html", error="Display name must be 1–60 characters."), 400
    if db.session.execute(select(User.id).where(User.email == email)).scalar_one_or_none() is not None:
        return render_template("signup.html", error="That email is already registered."), 409

    user = User(
        public_id=make_public_id(),
        email=email,
        password_hash=generate_password_hash(password),
        display_name=display_name,
    )
    db.session.add(user)
    db.session.commit()

    session.clear()
    login_user(user, remember=True)
    csrf_token()
    return redirect(url_for("index"))


@app.post("/logout")
@login_required
def logout_post():
    logout_user()
    session.clear()
    return redirect(url_for("login"))


@app.get("/api/bootstrap")
@json_login_required
def api_bootstrap():
    return jsonify(
        {
            "user": serialize_user(current_user, include_email=True),
            "contacts": [
                {
                    "id": contact.id,
                    "public_id": contact.owner.public_id,
                    "display_name": contact.owner.display_name,
                }
                for contact in sorted(current_user.contacts, key=lambda c: c.owner.display_name.lower())
            ],
            "items": [serialize_clip(item) for item in visible_items_for(current_user)],
        }
    )


@app.post("/api/contacts")
@json_login_required
def add_contact():
    data = request.get_json(silent=True) or {}
    public_id = str(data.get("public_id", "")).strip().upper()
    if not public_id:
        return jsonify({"error": "Enter a MySync ID."}), 400
    if public_id == current_user.public_id:
        return jsonify({"error": "You cannot add yourself."}), 400

    owner = db.session.execute(select(User).where(User.public_id == public_id)).scalar_one_or_none()
    if owner is None:
        return jsonify({"error": "No account exists with that MySync ID."}), 404

    existing = db.session.execute(
        select(Contact).where(Contact.viewer_id == current_user.id, Contact.owner_id == owner.id)
    ).scalar_one_or_none()
    if existing:
        return jsonify({"error": "That user is already in your list."}), 409

    db.session.add(Contact(viewer_id=current_user.id, owner_id=owner.id))
    db.session.commit()
    socketio.emit("data_changed")
    return jsonify(
        {
            "ok": True,
            "contact": {
                "id": existing.id if existing else Contact.query.filter_by(viewer_id=current_user.id, owner_id=owner.id).first().id,
                "public_id": owner.public_id,
                "display_name": owner.display_name,
            },
        }
    ), 201


@app.delete("/api/contacts/<int:contact_id>")
@json_login_required
def remove_contact(contact_id):
    contact = db.session.execute(
        select(Contact).where(Contact.id == contact_id, Contact.viewer_id == current_user.id)
    ).scalar_one_or_none()
    if contact is None:
        return jsonify({"error": "Contact not found."}), 404

    db.session.delete(contact)
    db.session.commit()
    socketio.emit("data_changed")
    return jsonify({"ok": True})


@app.post("/api/items")
@json_login_required
def add_item():
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    device_id = str(data.get("device_id", "")).strip()
    device_name = str(data.get("device_name", "")).strip() or "Unnamed device"
    is_shared = bool(data.get("is_shared", False))

    if not isinstance(content, str) or not content.strip():
        return jsonify({"error": "Clipboard content cannot be empty."}), 400
    if not device_id or len(device_id) > 100:
        return jsonify({"error": "A valid device ID is required."}), 400
    if len(device_name) > 80:
        return jsonify({"error": "Device name is too long."}), 400
    if len(content) > 1_000_000:
        return jsonify({"error": "Clipboard is too large (1 MB maximum)."}), 413

    item = ClipboardItem(
        owner=current_user,
        device_id=device_id,
        device_name=device_name,
        content=content,
        is_shared=is_shared,
    )
    db.session.add(item)
    db.session.commit()

    socketio.emit("data_changed")
    return jsonify(serialize_clip(item)), 201


@app.patch("/api/items/<int:item_id>")
@json_login_required
def update_item(item_id):
    item = db.session.execute(
        select(ClipboardItem).where(ClipboardItem.id == item_id, ClipboardItem.user_id == current_user.id)
    ).scalar_one_or_none()
    if item is None:
        return jsonify({"error": "Clipboard item not found."}), 404

    data = request.get_json(silent=True) or {}
    if "is_shared" in data:
        item.is_shared = bool(data["is_shared"])
    if "device_name" in data:
        new_name = str(data["device_name"]).strip()
        if not new_name or len(new_name) > 80:
            return jsonify({"error": "Device name is invalid."}), 400
        item.device_name = new_name

    db.session.commit()
    socketio.emit("data_changed")
    return jsonify(serialize_clip(item))


@app.delete("/api/items/<int:item_id>")
@json_login_required
def delete_item(item_id):
    item = db.session.execute(
        select(ClipboardItem).where(ClipboardItem.id == item_id, ClipboardItem.user_id == current_user.id)
    ).scalar_one_or_none()
    if item is None:
        return jsonify({"error": "Clipboard item not found."}), 404

    db.session.delete(item)
    db.session.commit()
    socketio.emit("data_changed")
    return jsonify({"ok": True})


@socketio.on("connect")
def socket_connected():
    if not current_user.is_authenticated:
        return False


@app.errorhandler(413)
def request_too_large(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Request is too large."}), 413
    return "Request is too large.", 413


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"MySync running on http://0.0.0.0:{port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=False)
