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
from app.services.admin_stores import approve_store_verification
from app.services.partner_onboarding import PartnerOnboardingValidationError


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


def _add_document(
    session,
    app,
    onboarding: StoreOnboarding,
    *,
    document_type: str,
    file_name: str,
) -> StoreOnboardingDocument:
    payload = f"%PDF-1.4\n% {file_name}\n%%EOF\n".encode()
    storage_key = f"onboarding/tests/{uuid.uuid4().hex}.pdf"
    path = Path(app.config["PARTNER_DOCUMENT_UPLOAD_DIR"]) / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    document = StoreOnboardingDocument(
        onboarding_id=onboarding.id,
        storage_key=storage_key,
        file_name=file_name,
        mime_type="application/pdf",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        document_type=document_type,
        status=StoreOnboardingDocumentStatus.PENDING_REVIEW,
    )
    session.add(document)
    session.flush()
    return document


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


def test_multiple_document_corrections_are_saved_in_one_review_and_visible_to_seller(
    app, session, client
):
    seller, _store, onboarding, identity = _submitted_onboarding(session, app)
    bank = _add_document(
        session,
        app,
        onboarding,
        document_type="BANK_CERTIFICATE",
        file_name="certificado-bancario.pdf",
    )
    registration = _add_document(
        session,
        app,
        onboarding,
        document_type="CORPORATE_ACTS_CERTIFICATE",
        file_name="actos-societarios.pdf",
    )
    unaffected = _add_document(
        session,
        app,
        onboarding,
        document_type="PASSPORT",
        file_name="pasaporte.pdf",
    )
    staff = _user(session, staff=True, name="Alejandro Admin")
    session.commit()
    reviews_before = len(onboarding.reviews)

    _login(client, staff)
    response = client.post(
        f"/admin/stores/{onboarding.id}/request-corrections",
        data={
            "expected_updated_at": onboarding.updated_at.isoformat(),
            "document_issue_target": [str(identity.id), str(bank.id), str(registration.id)],
            "document_issue_reason": [
                "DOCUMENT_UNREADABLE",
                "DOCUMENT_INCORRECT",
                "DOCUMENT_INCOMPLETE",
            ],
            "document_issue_message": [
                "La fotografía está borrosa.",
                "El titular no coincide con los datos bancarios.",
                "Falta la segunda página del documento.",
            ],
            "comments": "Revisión documental del expediente.",
        },
    )
    assert response.status_code == 302
    assert "status=corrections" in response.headers["Location"]

    session.expire_all()
    onboarding = session.get(StoreOnboarding, onboarding.id)
    review = onboarding.reviews[-1]
    assert onboarding.status == StoreOnboardingStatus.CORRECTIONS_REQUESTED
    assert len(onboarding.reviews) == reviews_before + 1
    assert len(review.issues_snapshot) == 3
    assert review.comments == "Revisión documental del expediente."
    assert [issue["target_id"] for issue in review.issues_snapshot] == [
        str(identity.id),
        str(bank.id),
        str(registration.id),
    ]
    for document_id, message in (
        (identity.id, "La fotografía está borrosa."),
        (bank.id, "El titular no coincide con los datos bancarios."),
        (registration.id, "Falta la segunda página del documento."),
    ):
        document = session.get(StoreOnboardingDocument, document_id)
        assert document.status == StoreOnboardingDocumentStatus.REJECTED
        assert document.admin_comment == message
    assert session.get(StoreOnboardingDocument, unaffected.id).status == StoreOnboardingDocumentStatus.PENDING_REVIEW

    _login(client, seller)
    seller_status = client.get("/partners/onboarding/status").get_data(as_text=True)
    assert "Registro mercantil" in seller_status
    assert "Documento ilegible" in seller_status
    assert "La fotografía está borrosa." in seller_status
    assert "Certificado bancario" in seller_status
    assert "Documento incorrecto" in seller_status
    assert "El titular no coincide con los datos bancarios." in seller_status
    assert "Certificado de actos societarios" in seller_status
    assert "Documento incompleto" in seller_status


def test_multiple_document_corrections_reject_foreign_and_duplicate_payload_atomically(
    app, session, client
):
    _seller, _store, onboarding, local_document = _submitted_onboarding(session, app)
    _other_seller, _other_store, _other_onboarding, foreign_document = _submitted_onboarding(
        session, app, name="Tienda Fuera de Alcance"
    )
    staff = _user(session, staff=True)
    session.commit()
    reviews_before = len(onboarding.reviews)
    expected_updated_at = onboarding.updated_at.isoformat()
    _login(client, staff)

    foreign = client.post(
        f"/admin/stores/{onboarding.id}/request-corrections",
        data={
            "expected_updated_at": expected_updated_at,
            "document_issue_target": [str(local_document.id), str(foreign_document.id)],
            "document_issue_reason": ["DOCUMENT_UNREADABLE", "DOCUMENT_INCORRECT"],
            "document_issue_message": ["Documento local borroso.", "Documento ajeno."],
        },
    )
    assert foreign.status_code == 302
    session.expire_all()
    assert session.get(StoreOnboarding, onboarding.id).status == StoreOnboardingStatus.SUBMITTED
    assert len(session.get(StoreOnboarding, onboarding.id).reviews) == reviews_before
    assert session.get(StoreOnboardingDocument, local_document.id).status == StoreOnboardingDocumentStatus.PENDING_REVIEW

    duplicate = client.post(
        f"/admin/stores/{onboarding.id}/request-corrections",
        data={
            "expected_updated_at": expected_updated_at,
            "document_issue_target": [str(local_document.id), str(local_document.id)],
            "document_issue_reason": ["DOCUMENT_UNREADABLE", "DOCUMENT_UNREADABLE"],
            "document_issue_message": ["Primera fila.", "Fila repetida."],
        },
    )
    assert duplicate.status_code == 302
    session.expire_all()
    assert session.get(StoreOnboarding, onboarding.id).status == StoreOnboardingStatus.SUBMITTED
    assert len(session.get(StoreOnboarding, onboarding.id).reviews) == reviews_before
    assert session.get(StoreOnboardingDocument, local_document.id).status == StoreOnboardingDocumentStatus.PENDING_REVIEW

    different_reasons = client.post(
        f"/admin/stores/{onboarding.id}/request-corrections",
        data={
            "expected_updated_at": expected_updated_at,
            "document_issue_target": [str(local_document.id), str(local_document.id)],
            "document_issue_reason": ["DOCUMENT_UNREADABLE", "DOCUMENT_INCOMPLETE"],
            "document_issue_message": ["El archivo está borroso.", "También falta una página."],
        },
    )
    assert different_reasons.status_code == 302
    assert "status=corrections" in different_reasons.headers["Location"]
    session.expire_all()
    onboarding = session.get(StoreOnboarding, onboarding.id)
    local_document = session.get(StoreOnboardingDocument, local_document.id)
    assert onboarding.status == StoreOnboardingStatus.CORRECTIONS_REQUESTED
    assert len(onboarding.reviews) == reviews_before + 1
    assert len(onboarding.reviews[-1].issues_snapshot) == 2
    assert local_document.admin_comment == "El archivo está borroso.\nTambién falta una página."


def test_partial_multiple_document_replacement_blocks_resubmission_and_keeps_links(
    app, session, client
):
    seller, _store, onboarding, identity = _submitted_onboarding(session, app)
    bank = _add_document(
        session,
        app,
        onboarding,
        document_type="BANK_CERTIFICATE",
        file_name="banco-original.pdf",
    )
    staff = _user(session, staff=True)
    session.commit()

    _login(client, staff)
    client.post(
        f"/admin/stores/{onboarding.id}/request-corrections",
        data={
            "expected_updated_at": onboarding.updated_at.isoformat(),
            "document_issue_target": [str(identity.id), str(bank.id)],
            "document_issue_reason": ["DOCUMENT_UNREADABLE", "DOCUMENT_INCORRECT"],
            "document_issue_message": ["Reemplaza la identidad.", "Reemplaza el certificado."],
        },
    )

    _login(client, seller)
    first_replacement = client.post(
        "/partners/onboarding/step/4",
        data={
            "document_type": "COMPANY_REGISTRATION",
            "replaces_document_id": str(identity.id),
            "documents": (io.BytesIO(b"%PDF-1.4\n% identity replacement\n%%EOF\n"), "identidad-nueva.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert first_replacement.status_code == 302
    assert client.post("/partners/onboarding/review").status_code == 302
    session.expire_all()
    assert session.get(StoreOnboarding, onboarding.id).status == StoreOnboardingStatus.CORRECTIONS_REQUESTED

    second_replacement = client.post(
        "/partners/onboarding/step/4",
        data={
            "document_type": "BANK_CERTIFICATE",
            "replaces_document_id": str(bank.id),
            "documents": (io.BytesIO(b"%PDF-1.4\n% bank replacement\n%%EOF\n"), "banco-nuevo.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert second_replacement.status_code == 302
    assert client.post("/partners/onboarding/review").status_code == 302

    session.expire_all()
    onboarding = session.get(StoreOnboarding, onboarding.id)
    assert onboarding.status == StoreOnboardingStatus.SUBMITTED
    replacements = {
        document.file_name: document.replaces_document_id
        for document in onboarding.documents
        if document.replaces_document_id is not None
    }
    assert replacements["identidad-nueva.pdf"] == identity.id
    assert replacements["banco-nuevo.pdf"] == bank.id


def test_approval_without_current_documents_uses_correct_utf8_message(app, session):
    _seller, _store, onboarding, document = _submitted_onboarding(session, app)
    staff = _user(session, staff=True)
    session.delete(document)
    session.commit()

    with pytest.raises(PartnerOnboardingValidationError) as exc_info:
        approve_store_verification(
            session,
            onboarding_id=onboarding.id,
            reviewer_user_id=staff.id,
            checklist_values=[
                "identity",
                "address_contact",
                "banking",
                "documents",
                "no_pending_corrections",
            ],
            expected_updated_at=onboarding.updated_at.isoformat(),
            comments=None,
        )
    assert str(exc_info.value) == "La solicitud no tiene documentación vigente para aprobar."
    assert not any(marker in str(exc_info.value) for marker in ("Ã", "Â", "�"))
    session.rollback()


def test_structured_field_corrections_remain_supported(app, session, client):
    _seller, _store, onboarding, document = _submitted_onboarding(session, app)
    staff = _user(session, staff=True)
    session.commit()
    original_bank_account = onboarding.bank_account_number

    _login(client, staff)
    response = client.post(
        f"/admin/stores/{onboarding.id}/request-corrections",
        data={
            "expected_updated_at": onboarding.updated_at.isoformat(),
            "field_issue_code": ["BANK_DATA_INCORRECT", "ADDRESS_INCOMPLETE"],
            "comments": "Verifica los datos señalados.",
        },
    )
    assert response.status_code == 302

    session.expire_all()
    onboarding = session.get(StoreOnboarding, onboarding.id)
    issues = onboarding.reviews[-1].issues_snapshot
    assert [issue["target_type"] for issue in issues] == ["FIELD", "FIELD"]
    assert issues[0]["field"] == "bank_account_number"
    assert issues[0]["previous_value"] == original_bank_account
    assert issues[1]["field"] == "address"
    assert session.get(StoreOnboardingDocument, document.id).status == StoreOnboardingDocumentStatus.PENDING_REVIEW


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
