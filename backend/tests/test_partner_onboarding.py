from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import Store, StoreContractAcceptance, StoreMember, StoreOnboarding, User
from app.models.enums import (
    StoreContractAcceptanceStatus,
    StoreMemberRole,
    StoreOnboardingStatus,
    StoreStatus,
    UserStatus,
)
from app.services.mail import mail_service
from app.services.partner_onboarding import review_onboarding


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app, tmp_path):
    app.config["MAIL_BACKEND"] = "memory"
    app.config["PHONE_OTP_PEPPER"] = "test-only-phone-otp-pepper"
    app.config["PARTNER_DOCUMENT_UPLOAD_DIR"] = str(tmp_path / "partner-documents")
    app.config["PARTNER_CONTRACT_UPLOAD_DIR"] = str(tmp_path / "partner-contracts")
    mail_service.outbox.clear()
    test_client = app.test_client()
    yield test_client
    db.session.remove()
    mail_service.outbox.clear()


def _user(session, *, email="partner@test.local", password="correct horse battery staple"):
    user = User(
        public_code=f"ECV-U-{uuid.uuid4().hex[:8].upper()}",
        email=email,
        email_normalized=email.casefold(),
        password_hash=generate_password_hash(password),
        full_name="Partner Ecuvel",
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _login(client, email="partner@test.local", password="correct horse battery staple"):
    return client.post(
        "/iniciar-sesion",
        data={"email": email, "password": password, "next": "/partners"},
        follow_redirects=False,
    )


def test_partner_onboarding_happy_path_creates_store_and_accepts_contract(client, session, monkeypatch):
    user = _user(session)
    session.commit()

    anonymous = client.get("/partners")
    assert anonymous.status_code == 302
    assert "/iniciar-sesion" in anonymous.headers["Location"]

    assert _login(client).status_code == 302
    assert client.get("/partners").status_code == 302

    assert client.post(
        "/partners/onboarding/step/1",
        data={"store_name": "CrisBeauty", "legal_id_number": "210049391"},
    ).status_code == 302
    assert client.post(
        "/partners/onboarding/step/2",
        data={
            "province": "Orellana",
            "city": "Puerto Francisco de Orellana",
            "address": "Av. 9 de octubre y Miguel Gamboa",
        },
    ).status_code == 302
    assert client.post(
        "/partners/onboarding/step/3",
        data={"whatsapp_or_nickname": "0999330014"},
    ).status_code == 302
    assert client.post(
        "/partners/onboarding/step/4",
        data={
            "documents": (io.BytesIO(b"%PDF-1.4\n% ecuvel test\n%%EOF\n"), "registro.pdf"),
        },
        content_type="multipart/form-data",
    ).status_code == 302
    assert client.post(
        "/partners/onboarding/step/5",
        data={
            "bank_account_owner": "CrisBeauty",
            "bank_account_number": "000123456789",
            "bank_name": "Banco de prueba",
            "bank_id_number": "210049391",
            "bank_email": "pagos@crisbeauty.test",
        },
    ).status_code == 302
    assert client.post("/partners/onboarding/review").status_code == 302

    session.expire_all()
    onboarding = session.scalar(select(StoreOnboarding).where(StoreOnboarding.user_id == user.id))
    assert onboarding is not None
    assert onboarding.status == StoreOnboardingStatus.SUBMITTED
    assert len(onboarding.documents) == 1
    store = session.get(Store, onboarding.store_id)
    assert store is not None
    assert store.status == StoreStatus.PENDING_REVIEW
    member = session.scalar(select(StoreMember).where(StoreMember.store_id == store.id))
    assert member.user_id == user.id
    assert member.role == StoreMemberRole.OWNER

    review_onboarding(
        session=session,
        onboarding_id=onboarding.id,
        reviewer_user_id=None,
        decision="approve",
        comments="Datos verificados.",
    )
    session.commit()

    assert client.get("/partners/contract").status_code == 200
    monkeypatch.setattr("app.services.partner_onboarding.secrets.randbelow", lambda upper: 123456)
    assert client.post("/partners/contract/otp", data={"action": "send"}).status_code == 302
    assert mail_service.outbox
    assert "123456" in mail_service.outbox[-1].body

    response = client.post(
        "/partners/contract/otp",
        data={
            "otp_code": "123456",
            "truthful": "1",
            "terms": "1",
            "fees": "1",
            "obligations": "1",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/partners/products")

    session.expire_all()
    onboarding = session.get(StoreOnboarding, onboarding.id)
    store = session.get(Store, store.id)
    acceptance = session.scalar(select(StoreContractAcceptance))
    assert onboarding.status == StoreOnboardingStatus.COMPLETED
    assert store.status == StoreStatus.ACTIVE
    assert store.is_verified is True
    assert acceptance.status == StoreContractAcceptanceStatus.ACCEPTED
    assert acceptance.pdf_storage_key
    assert client.get("/partners/products").status_code == 200
