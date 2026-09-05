from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import User
from app.models.enums import UserStatus
from app.services.safe_redirects import safe_local_redirect


pytestmark = pytest.mark.integration
TODAY = date(2026, 9, 4)


@pytest.fixture
def client(app):
    test_client = app.test_client()
    yield test_client
    db.session.remove()


@pytest.fixture(autouse=True)
def fixed_ecuador_date(monkeypatch):
    monkeypatch.setattr(
        "app.services.age_eligibility.ecuador_local_date",
        lambda: TODAY,
    )
    monkeypatch.setattr(
        "app.services.user_profiles.ecuador_local_date",
        lambda: TODAY,
    )


def _user(
    session: Session,
    *,
    birth_date: date | None,
    password_hash: str | None = None,
) -> User:
    token = uuid.uuid4().hex[:12]
    user = User(
        public_code=f"AGE-{token}",
        email=f"age-{token}@test.local",
        password_hash=password_hash,
        full_name="Age Gate Test",
        birth_date=birth_date,
        email_verified_at=datetime.now(timezone.utc),
        status=UserStatus.ACTIVE,
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user


def _login_as(client, user_id) -> None:
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(user_id)
        browser_session["_fresh"] = True


def test_guest_age_gate_uses_existing_login_flow(client):
    response = client.get("/verificar-edad?next=/checkout")

    assert response.status_code == 302
    assert "/iniciar-sesion" in response.headers["Location"]


def test_missing_birth_date_shows_form_and_safe_back_link(
    client, session: Session
):
    user = _user(session, birth_date=None)
    _login_as(client, user.id)

    response = client.get(
        "/verificar-edad?next=/checkout&back=/carrito"
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="birth_date"' in body
    assert 'name="csrf_token"' in body
    assert 'href="/carrito"' in body
    assert "no podrá modificarse" in body


@pytest.mark.parametrize(
    "birth_date_value",
    ("2008-09-04", "1990-01-01"),
)
def test_missing_birth_date_accepts_adult_and_continues_to_safe_next(
    client,
    session: Session,
    birth_date_value: str,
):
    user = _user(session, birth_date=None, password_hash=None)
    user_id = user.id
    _login_as(client, user_id)

    response = client.post(
        "/verificar-edad",
        data={
            "birth_date": birth_date_value,
            "next": "/producto/test?variant=ABC",
            "back": "/carrito",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        "/producto/test?variant=ABC"
    )
    session.expire_all()
    assert session.get(User, user_id).birth_date == date.fromisoformat(
        birth_date_value
    )
    assert session.get(User, user_id).password_hash is None
    assert session.get(User, user_id).status == UserStatus.ACTIVE
    assert (
        client.get("/perfil").get_data(as_text=True).find(
            f'value="{birth_date_value}"'
        )
        >= 0
    )


@pytest.mark.parametrize(
    ("birth_date_value", "message"),
    (
        ("2008-09-05", "al menos 18 años"),
        ("2026-09-05", "futuro"),
        ("not-a-date", "fecha de nacimiento válida"),
    ),
)
def test_missing_birth_date_rejects_invalid_submission_without_write(
    client,
    session: Session,
    birth_date_value: str,
    message: str,
):
    user = _user(session, birth_date=None)
    user_id = user.id
    _login_as(client, user_id)

    response = client.post(
        "/verificar-edad",
        data={"birth_date": birth_date_value, "next": "/checkout"},
    )

    assert response.status_code == 400
    assert message in response.get_data(as_text=True)
    session.expire_all()
    assert session.get(User, user_id).birth_date is None


def test_existing_adult_get_redirects_and_forged_post_does_not_overwrite(
    client, session: Session
):
    original = date(2000, 1, 1)
    user = _user(session, birth_date=original)
    user_id = user.id
    _login_as(client, user_id)

    get_response = client.get("/verificar-edad?next=/checkout")
    post_response = client.post(
        "/verificar-edad",
        data={"birth_date": "1990-01-01", "next": "/checkout"},
    )

    assert get_response.status_code == 302
    assert get_response.headers["Location"].endswith("/checkout")
    assert post_response.status_code == 302
    assert post_response.headers["Location"].endswith("/checkout")
    session.expire_all()
    assert session.get(User, user_id).birth_date == original


@pytest.mark.parametrize(
    ("concurrent_birth_date", "expected_status"),
    ((date(2000, 1, 1), 302), (date(2010, 1, 1), 400)),
)
def test_post_rechecks_birth_date_saved_after_form_was_rendered(
    client,
    session: Session,
    concurrent_birth_date: date,
    expected_status: int,
):
    user = _user(session, birth_date=None)
    user_id = user.id
    _login_as(client, user_id)
    assert client.get("/verificar-edad?next=/checkout").status_code == 200

    user.birth_date = concurrent_birth_date
    session.commit()
    response = client.post(
        "/verificar-edad",
        data={"birth_date": "1990-01-01", "next": "/checkout"},
    )

    assert response.status_code == expected_status
    session.expire_all()
    assert session.get(User, user_id).birth_date == concurrent_birth_date


def test_existing_minor_is_blocked_without_edit_and_forged_post_cannot_replace(
    client, session: Session
):
    original = date(2010, 1, 1)
    user = _user(session, birth_date=original)
    user_id = user.id
    _login_as(client, user_id)

    get_response = client.get(
        "/verificar-edad?next=/checkout&back=/carrito"
    )
    get_body = get_response.get_data(as_text=True)
    post_response = client.post(
        "/verificar-edad",
        data={"birth_date": "1990-01-01", "next": "/checkout"},
    )

    assert get_response.status_code == 200
    assert "Según la fecha de nacimiento registrada" in get_body
    assert 'name="birth_date"' not in get_body
    assert post_response.status_code == 400
    assert 'name="birth_date"' not in post_response.get_data(as_text=True)
    session.expire_all()
    assert session.get(User, user_id).birth_date == original


@pytest.mark.parametrize(
    "unsafe",
    (
        "https://evil.example",
        "http://evil.example",
        "//evil.example",
        "javascript:alert(1)",
        "data:text/plain,hello",
        "/\\evil.example",
        "/%5cevil.example",
        "/%2f%2fevil.example",
    ),
)
def test_safe_local_redirect_rejects_external_and_ambiguous_targets(unsafe):
    assert safe_local_redirect(unsafe, fallback="/seguro") == "/seguro"


def test_safe_local_redirect_preserves_local_query_and_drops_fragment():
    assert (
        safe_local_redirect(
            "/producto/test?variant=ABC#details",
            fallback="/seguro",
        )
        == "/producto/test?variant=ABC"
    )


def test_external_next_and_back_use_local_fallbacks(client, session: Session):
    user = _user(session, birth_date=None)
    _login_as(client, user.id)

    response = client.get(
        "/verificar-edad?next=https://evil.example&back=//evil.example"
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="next" value="/"' in body
    assert 'href="/carrito"' in body
    assert "evil.example" not in body


def test_post_revalidates_hidden_next_value(client, session: Session):
    user = _user(session, birth_date=date(2000, 1, 1))
    _login_as(client, user.id)

    response = client.post(
        "/verificar-edad",
        data={
            "birth_date": "1990-01-01",
            "next": "https://evil.example",
            "back": "/carrito",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert "evil.example" not in response.headers["Location"]


def test_age_gate_post_remains_csrf_protected(client, app, session: Session):
    user = _user(session, birth_date=None)
    _login_as(client, user.id)
    previous = app.config["WTF_CSRF_ENABLED"]
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = client.post(
            "/verificar-edad",
            data={"birth_date": "1990-01-01", "next": "/checkout"},
        )
        assert response.status_code == 400
    finally:
        app.config["WTF_CSRF_ENABLED"] = previous
