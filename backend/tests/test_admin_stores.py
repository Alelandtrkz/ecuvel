from __future__ import annotations

import hashlib
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import (
    Store,
    StoreMember,
    StoreOnboarding,
    StoreOnboardingDocument,
    StoreVerificationReview,
    User,
)
from app.models.enums import (
    StoreMemberRole,
    StoreOnboardingDocumentStatus,
    StoreOnboardingStage,
    StoreOnboardingStatus,
    StoreStatus,
    StoreVerificationDecision,
    UserStatus,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app, tmp_path):
    app.config["PARTNER_DOCUMENT_UPLOAD_DIR"] = str(tmp_path / "partner-documents")
    app.config["PARTNER_CONTRACT_UPLOAD_DIR"] = str(tmp_path / "partner-contracts")
    yield app.test_client()
    db.session.remove()


def _user(session, *, staff: bool = False, name: str = "Usuario prueba") -> User:
    token = uuid.uuid4().hex[:10]
    user = User(
        public_code=f"USR-{token}",
        email=f"{token}@test.local",
        email_normalized=f"{token}@test.local",
        password_hash="test",
        full_name=name,
        status=UserStatus.ACTIVE,
        is_active=True,
        is_ecuvel_staff=staff,
        email_verified_at=datetime.now(timezone.utc),
    )
    session.add(user)
    session.flush()
    return user


def _login(client, user: User) -> None:
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


def _submitted_onboarding(session, app, *, name: str = "Tienda Nova"):
    seller = _user(session, name="Seller Nova")
    store = Store(
        public_code=f"STR-{uuid.uuid4().hex[:10]}",
        name=name,
        slug=f"store-{uuid.uuid4().hex[:10]}",
        legal_name=name,
        tax_id=uuid.uuid4().hex[:13],
        status=StoreStatus.PENDING_REVIEW,
        is_verified=False,
    )
    session.add(store)
    session.flush()
    onboarding = StoreOnboarding(
        user_id=seller.id,
        store_id=store.id,
        status=StoreOnboardingStatus.SUBMITTED,
        current_stage=StoreOnboardingStage.WAITING_VERIFICATION,
        current_step=5,
        store_name=name,
        legal_id_number=store.tax_id,
        province="Pichincha",
        city="Quito",
        address="Av. República 123",
        whatsapp_or_nickname="0999001122",
        bank_account_owner="Seller Nova",
        bank_account_number="2200112233",
        bank_name="Banco Pichincha",
        bank_id_number=store.tax_id,
        bank_email="pagos@nova.test",
        submitted_at=datetime.now(timezone.utc),
    )
    session.add(onboarding)
    session.flush()
    session.add(StoreMember(
        store_id=store.id,
        user_id=seller.id,
        role=StoreMemberRole.OWNER,
        is_active=True,
    ))
    payload = b"%PDF-1.4\n% store moderation test\n%%EOF\n"
    storage_key = f"onboarding/tests/{uuid.uuid4().hex}.pdf"
    path = Path(app.config["PARTNER_DOCUMENT_UPLOAD_DIR"]) / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    document = StoreOnboardingDocument(
        onboarding_id=onboarding.id,
        storage_key=storage_key,
        file_name="registro.pdf",
        mime_type="application/pdf",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        document_type="COMPANY_REGISTRATION",
        status=StoreOnboardingDocumentStatus.PENDING_REVIEW,
    )
    session.add_all([
        document,
        StoreVerificationReview(
            onboarding_id=onboarding.id,
            decision=StoreVerificationDecision.PENDING,
            comments="Solicitud enviada por el partner.",
        ),
    ])
    session.commit()
    return seller, store, onboarding, document


def test_admin_store_moderation_access_list_and_private_document(app, session, client):
    seller, store, onboarding, document = _submitted_onboarding(session, app)
    _other_seller, _other_store, other_onboarding, _other_document = _submitted_onboarding(
        session, app, name="Tienda Alterna"
    )
    staff = _user(session, staff=True, name="Moderadora ECUVEL")
    session.commit()
    review_count = len(onboarding.reviews)

    assert client.get("/admin/stores").status_code == 302
    _login(client, seller)
    assert client.get("/admin/stores").status_code == 403

    _login(client, staff)
    response = client.get("/admin/stores?q=Nova&province=Pichincha")
    assert response.status_code == 200
    assert "Tienda Nova" in response.get_data(as_text=True)
    assert client.get(f"/admin/stores?q={store.tax_id}").status_code == 200
    assert "Tienda Nova" in client.get(f"/admin/stores?q={seller.email}").get_data(as_text=True)
    detail = client.get(f"/admin/stores/{onboarding.id}")
    assert detail.status_code == 200
    assert "Aprobar verificación" in detail.get_data(as_text=True)
    assert "Rechazar" not in detail.get_data(as_text=True)

    private = client.get(f"/admin/stores/{onboarding.id}/documents/{document.id}")
    assert private.status_code == 200
    assert private.data.startswith(b"%PDF-")
    assert private.headers["Cache-Control"] == "private, no-store"
    assert private.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get(
        f"/admin/stores/{other_onboarding.id}/documents/{document.id}"
    ).status_code == 404
    session.expire_all()
    assert len(session.get(StoreOnboarding, onboarding.id).reviews) == review_count


def test_corrections_replacement_resubmission_and_approval_keep_store_pending(
    app, session, client
):
    seller, store, onboarding, document = _submitted_onboarding(session, app)
    staff = _user(session, staff=True, name="Moderadora ECUVEL")
    session.commit()
    expected = onboarding.updated_at.isoformat()

    _login(client, staff)
    corrected = client.post(
        f"/admin/stores/{onboarding.id}/request-corrections",
        data={
            "expected_updated_at": expected,
            "issue_code": "DOCUMENT_UNREADABLE",
            "document_id": str(document.id),
            "comments": "Sube una copia legible del registro mercantil.",
        },
    )
    assert corrected.status_code == 302
    assert "status=corrections" in corrected.headers["Location"]

    session.expire_all()
    onboarding = session.get(StoreOnboarding, onboarding.id)
    document = session.get(StoreOnboardingDocument, document.id)
    store = session.get(Store, store.id)
    assert onboarding.status == StoreOnboardingStatus.CORRECTIONS_REQUESTED
    assert document.status == StoreOnboardingDocumentStatus.REJECTED
    assert store.status == StoreStatus.PENDING_REVIEW
    assert store.is_verified is False
    review = session.scalar(
        select(StoreVerificationReview)
        .where(StoreVerificationReview.onboarding_id == onboarding.id)
        .order_by(StoreVerificationReview.created_at.desc())
    )
    assert review.issues_snapshot[0]["target_id"] == str(document.id)
    assert review.reviewer_user_id == staff.id
    assert review.comments == "Sube una copia legible del registro mercantil."

    _login(client, seller)
    status = client.get("/partners/onboarding/status")
    assert status.status_code == 200
    assert "Reemplazar archivo" in status.get_data(as_text=True)
    assert client.post("/partners/onboarding/review").status_code == 302
    session.expire_all()
    assert session.get(StoreOnboarding, onboarding.id).status == StoreOnboardingStatus.CORRECTIONS_REQUESTED
    replacement = client.post(
        "/partners/onboarding/step/4",
        data={
            "document_type": "COMPANY_REGISTRATION",
            "replaces_document_id": str(document.id),
            "documents": (
                io.BytesIO(b"%PDF-1.4\n% legible replacement\n%%EOF\n"),
                "registro-legible.pdf",
            ),
        },
        content_type="multipart/form-data",
    )
    assert replacement.status_code == 302
    assert client.post("/partners/onboarding/review").status_code == 302

    session.expire_all()
    onboarding = session.get(StoreOnboarding, onboarding.id)
    documents = session.scalars(
        select(StoreOnboardingDocument)
        .where(StoreOnboardingDocument.onboarding_id == onboarding.id)
        .order_by(StoreOnboardingDocument.created_at)
    ).all()
    assert onboarding.status == StoreOnboardingStatus.SUBMITTED
    assert len(documents) == 2
    assert documents[0].status == StoreOnboardingDocumentStatus.REJECTED
    assert documents[1].replaces_document_id == documents[0].id
    assert documents[1].status == StoreOnboardingDocumentStatus.PENDING_REVIEW

    _login(client, staff)
    approved = client.post(
        f"/admin/stores/{onboarding.id}/approve",
        data={
            "expected_updated_at": onboarding.updated_at.isoformat(),
            "checklist": [
                "identity",
                "address_contact",
                "banking",
                "documents",
                "no_pending_corrections",
            ],
            "comments": "Verificación completa.",
        },
    )
    assert approved.status_code == 302
    assert "status=contract" in approved.headers["Location"]

    session.expire_all()
    onboarding = session.get(StoreOnboarding, onboarding.id)
    store = session.get(Store, store.id)
    assert onboarding.status == StoreOnboardingStatus.APPROVED
    assert onboarding.current_stage == StoreOnboardingStage.CONTRACT_ACCEPTANCE
    assert store.status == StoreStatus.PENDING_REVIEW
    assert store.is_verified is False
    assert all(
        item.status != StoreOnboardingDocumentStatus.PENDING_REVIEW
        for item in onboarding.documents
    )


def test_admin_store_decision_rejects_stale_version(app, session, client):
    _seller, _store, onboarding, _document = _submitted_onboarding(session, app)
    staff = _user(session, staff=True)
    session.commit()
    stale = onboarding.updated_at.isoformat()
    onboarding.store_name = "Tienda actualizada mientras se revisaba"
    session.commit()

    _login(client, staff)
    response = client.post(
        f"/admin/stores/{onboarding.id}/approve",
        data={
            "expected_updated_at": stale,
            "checklist": [
                "identity",
                "address_contact",
                "banking",
                "documents",
                "no_pending_corrections",
            ],
        },
    )
    assert response.status_code == 302
    session.expire_all()
    assert session.get(StoreOnboarding, onboarding.id).status == StoreOnboardingStatus.SUBMITTED
