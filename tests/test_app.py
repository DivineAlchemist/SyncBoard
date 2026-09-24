import os
import tempfile

# Set the database before importing app.py.
test_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
test_db.close()
os.environ["DATABASE_URL"] = "sqlite:///" + test_db.name
os.environ["SECRET_KEY"] = "test-secret"
os.environ["SESSION_COOKIE_SECURE"] = "false"

from app import app, db, User, ClipboardItem  # noqa: E402


with app.app_context():
    db.drop_all()
    db.create_all()


def csrf(client):
    with client.session_transaction() as sess:
        return sess["csrf_token"]


def test_signup_login_and_sharing():
    client = app.test_client()

    response = client.get("/signup")
    assert response.status_code == 200

    token = csrf(client)
    response = client.post(
        "/signup",
        data={
            "csrf_token": token,
            "display_name": "Alice",
            "email": "alice@gmail.com",
            "password": "password123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        alice = User.query.filter_by(email="alice@gmail.com").first()
        assert alice is not None
        alice_id = alice.public_id

    token = csrf(client)
    response = client.post(
        "/api/items",
        json={
            "csrf_token": token,
            "content": "hello from Alice",
            "device_id": "device-1",
            "device_name": "Laptop",
            "is_shared": True,
        },
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 201

    client.post("/logout", data={"csrf_token": csrf(client)})
    token = csrf(client)
    response = client.post(
        "/signup",
        data={
            "csrf_token": token,
            "display_name": "Bob",
            "email": "bob@gmail.com",
            "password": "password123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    token = csrf(client)
    response = client.post(
        "/api/contacts",
        json={"public_id": alice_id},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 201

    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    data = response.get_json()
    assert data["items"][0]["content"] == "hello from Alice"

    with app.app_context():
        assert ClipboardItem.query.count() == 1
