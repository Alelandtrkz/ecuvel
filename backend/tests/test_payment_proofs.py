from __future__ import annotations

import io
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import (
    AdminAuditEvent,
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
    Order,
    PaymentAttempt,
    PaymentProof,
    SellerOrder,
    StaffProfile,
    User,
)
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentProofStatus,
    PaymentStatus,
    ReservationStatus,
    SellerOrderStatus,
    SellerOrderDecisionStatus,
    StaffEmploymentStatus,
    StaffIdentificationType,
    StaffRole,
    UserStatus,
)
from app.services.admin_permissions import user_has_permission
from app.services.payment_proofs import (
    InvalidPaymentProofTransitionError,
    PaymentProofExpiredError,
    PaymentProofIntegrityError,
    PaymentProofServiceError,
    PaymentProofUploadConflictError,
    review_payment_proof,
    submit_bank_transfer_proof,
)
from app.services.private_storage import (
    InvalidPaymentProofFileError,
    PaymentProofFileTooLargeError,
    PrivateStorageError,
    delete_private_file,
    private_file_path,
    stage_payment_proof,
)
from tests.factories import (
    create_catalog_and_stock,
    create_order_items,
    reserve_item,
)


pytestmark = pytest.mark.integration


def _image_bytes(image_format: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format=image_format)
    return output.getvalue()


PNG = _image_bytes("PNG")
JPEG = _image_bytes("JPEG")
PDF = b"%PDF-1.7\nvalid-private-proof"
PROOF_FORMAT_POLICY = {
    "allowed_extensions": {"jpg", "jpeg", "png", "pdf"},
    "allowed_media_types": {"image/jpeg", "image/png", "application/pdf"},
}


@pytest.fixture
def client(app):
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _upload(data=PNG, filename="proof.png", media="image/png"):
    return FileStorage(stream=io.BytesIO(data), filename=filename, content_type=media)


def _graph(session: Session, *, expired: bool = False):
    base = create_catalog_and_stock(session, stock=8)
    order_id, order_number, item_ids = create_order_items(session, base, [2])
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    reservation_ids = reserve_item(
        session, base, item_ids[0], expires_at=expires
    )
    attempt = PaymentAttempt(
        order_id=order_id,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.AWAITING_PROOF,
        amount=Decimal("20.00"),
        currency="USD",
        idempotency_key=f"checkout-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=expires,
    )
    admin = User(
        public_code=f"ADM-{uuid.uuid4().hex[:10]}",
        email=f"admin-{uuid.uuid4().hex[:10]}@test.local",
        password_hash="test",
        full_name="Admin Test",
        status=UserStatus.ACTIVE,
        is_ecuvel_staff=True,
    )
    session.add_all([attempt, admin]); session.flush()
    if expired:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        attempt.expires_at = past
        for reservation_id in reservation_ids:
            session.get(InventoryReservation, reservation_id).expires_at = past
        session.flush()
    return base, order_id, order_number, reservation_ids, attempt, admin


def _submit(session, tmp_path, attempt, buyer_id, *, key="upload-key"):
    staged = stage_payment_proof(
        _upload(),
        root=tmp_path,
        max_bytes=10 * 1024 * 1024,
        **PROOF_FORMAT_POLICY,
    )
    return submit_bank_transfer_proof(
        session=session,
        payment_attempt_id=attempt.id,
        staged_file=staged,
        upload_idempotency_key=key,
        storage_root=tmp_path,
        uploaded_by_user_id=buyer_id,
    )


def _assign_staff_profile(
    session: Session,
    user: User,
    *,
    role: StaffRole,
    employment_status: StaffEmploymentStatus = StaffEmploymentStatus.ACTIVE,
) -> StaffProfile:
    profile = StaffProfile(
        user_id=user.id,
        identification_type=StaffIdentificationType.OTHER,
        identification_number_normalized=f"ID-{uuid.uuid4().hex[:12]}",
        nationality_code="ECU",
        role=role,
        employment_status=employment_status,
        employment_started_at=date.today(),
    )
    session.add(profile)
    session.flush()
    return profile


@pytest.mark.parametrize(
    ("data", "filename", "media", "expected"),
    [(PNG, "a.png", "image/png", "image/png"), (JPEG, "a.jpeg", "image/jpeg", "image/jpeg"), (PDF, "a.pdf", "application/pdf", "application/pdf")],
)
def test_stage_accepts_supported_formats(tmp_path, data, filename, media, expected):
    staged = stage_payment_proof(
        _upload(data, filename, media),
        root=tmp_path,
        max_bytes=1000,
        **PROOF_FORMAT_POLICY,
    )
    assert staged.media_type == expected
    assert staged.size_bytes == len(data)
    assert staged.temporary_path.stat().st_size == len(data)


@pytest.mark.parametrize(
    ("data", "filename", "media"),
    [(b"", "a.png", "image/png"), (PNG, "a.jpg", "image/jpeg"), (PNG, "a.png", "application/pdf"), (b"<svg/>", "a.png", "image/png"), (PNG, "a.exe", "application/octet-stream"), (PNG, "../", "image/png")],
)
def test_stage_rejects_invalid_files(tmp_path, data, filename, media):
    with pytest.raises(InvalidPaymentProofFileError):
        stage_payment_proof(
            _upload(data, filename, media),
            root=tmp_path,
            max_bytes=1000,
            **PROOF_FORMAT_POLICY,
        )
    assert not list((tmp_path / ".staging").glob("*.tmp")) if (tmp_path / ".staging").exists() else True


def test_stage_rejects_oversize_and_cleans(tmp_path):
    with pytest.raises(PaymentProofFileTooLargeError):
        stage_payment_proof(
            _upload(PNG + b"x" * 100),
            root=tmp_path,
            max_bytes=20,
            **PROOF_FORMAT_POLICY,
        )
    assert not list((tmp_path / ".staging").glob("*.tmp"))


def test_stage_rejects_format_excluded_by_canonical_policy(tmp_path):
    with pytest.raises(InvalidPaymentProofFileError):
        stage_payment_proof(
            _upload(PNG, "proof.png", "image/png"),
            root=tmp_path,
            max_bytes=1000,
            allowed_extensions={"jpg", "jpeg", "pdf"},
            allowed_media_types={"image/jpeg", "application/pdf"},
        )


def test_stage_rejects_truncated_image_after_valid_signature(tmp_path):
    with pytest.raises(InvalidPaymentProofFileError):
        stage_payment_proof(
            _upload(PNG[:20], "truncated.png", "image/png"),
            root=tmp_path,
            max_bytes=1000,
            **PROOF_FORMAT_POLICY,
        )
    assert not list((tmp_path / ".staging").glob("*.tmp"))


def test_webp_is_not_accepted_when_business_config_excludes_it(tmp_path):
    with pytest.raises(InvalidPaymentProofFileError):
        stage_payment_proof(
            _upload(PNG, "proof.webp", "image/webp"),
            root=tmp_path,
            max_bytes=1000,
            **PROOF_FORMAT_POLICY,
        )


def test_private_path_rejects_traversal(tmp_path):
    with pytest.raises(PrivateStorageError):
        private_file_path(tmp_path, "../../secret.pdf")


def test_upload_changes_only_payment_and_proof(session: Session, tmp_path):
    base, order_id, _, reservations, attempt, _ = _graph(session)
    balance = session.get(InventoryBalance, base.balance_id)
    before = (balance.on_hand_quantity, balance.reserved_quantity)
    result = _submit(session, tmp_path, attempt, base.buyer_id)
    assert result.storage_path.is_file()
    assert attempt.status == PaymentStatus.PROCESSING
    assert session.get(Order, order_id).status == OrderStatus.PENDING_PAYMENT
    assert session.get(InventoryReservation, reservations[0]).status == ReservationStatus.ACTIVE
    assert (balance.on_hand_quantity, balance.reserved_quantity) == before


def test_same_upload_key_replays(session: Session, tmp_path):
    base, _, _, _, attempt, _ = _graph(session)
    first = _submit(session, tmp_path, attempt, base.buyer_id)
    second = _submit(session, tmp_path, attempt, base.buyer_id)
    assert second.replayed and second.proof_id == first.proof_id
    assert session.scalar(select(func.count(PaymentProof.id))) == 1


def test_different_upload_key_conflicts(session: Session, tmp_path):
    base, _, _, _, attempt, _ = _graph(session)
    _submit(session, tmp_path, attempt, base.buyer_id)
    with pytest.raises(PaymentProofUploadConflictError):
        _submit(session, tmp_path, attempt, base.buyer_id, key="different")


def test_upload_rejects_expired_reservations(session: Session, tmp_path):
    base, _, _, _, attempt, _ = _graph(session, expired=True)
    staged = stage_payment_proof(
        _upload(), root=tmp_path, max_bytes=1000, **PROOF_FORMAT_POLICY
    )
    with pytest.raises(PaymentProofExpiredError):
        submit_bank_transfer_proof(session=session, payment_attempt_id=attempt.id, staged_file=staged, upload_idempotency_key="expired", storage_root=tmp_path, uploaded_by_user_id=base.buyer_id)
    delete_private_file(staged.temporary_path)


def test_upload_rejects_wrong_buyer(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    staged = stage_payment_proof(
        _upload(), root=tmp_path, max_bytes=1000, **PROOF_FORMAT_POLICY
    )
    with pytest.raises(PaymentProofServiceError):
        submit_bank_transfer_proof(session=session, payment_attempt_id=attempt.id, staged_file=staged, upload_idempotency_key="wrong", storage_root=tmp_path, uploaded_by_user_id=admin.id)
    delete_private_file(staged.temporary_path)


def test_approve_consumes_without_reducing_on_hand(session: Session, tmp_path):
    base, order_id, _, reservation_ids, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    balance = session.get(InventoryBalance, base.balance_id); on_hand = balance.on_hand_quantity
    result = review_payment_proof(session=session, proof_id=proof.proof_id, decision="approve", reviewer_user_id=admin.id, storage_root=tmp_path)
    assert result.proof_status == PaymentProofStatus.APPROVED
    assert session.get(Order, order_id).status == OrderStatus.CONFIRMED
    assert session.get(InventoryReservation, reservation_ids[0]).status == ReservationStatus.CONSUMED
    assert balance.on_hand_quantity == on_hand
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == order_id)
    )
    assert seller_order.decision_status == SellerOrderDecisionStatus.PENDING
    assert seller_order.decision_available_at == attempt.approved_at
    assert seller_order.ship_by_at == attempt.approved_at + timedelta(hours=24)
    assert seller_order.estimated_delivery_to == attempt.approved_at + timedelta(hours=48)


def test_reject_releases_and_creates_movement(session: Session, tmp_path):
    base, order_id, _, reservation_ids, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    on_hand = session.get(InventoryBalance, base.balance_id).on_hand_quantity
    result = review_payment_proof(session=session, proof_id=proof.proof_id, decision="reject", reviewer_user_id=admin.id, storage_root=tmp_path, reason_code="OTHER", reason="No corresponde")
    assert result.proof_status == PaymentProofStatus.REJECTED
    assert session.get(Order, order_id).status == OrderStatus.CANCELLED
    assert session.get(InventoryReservation, reservation_ids[0]).status == ReservationStatus.RELEASED
    assert session.scalar(select(func.count(InventoryMovement.id))) == 2
    assert session.get(InventoryBalance, base.balance_id).on_hand_quantity == on_hand


def test_reject_requires_reason(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    with pytest.raises(PaymentProofServiceError):
        review_payment_proof(session=session, proof_id=proof.proof_id, decision="reject", reviewer_user_id=admin.id, storage_root=tmp_path)


@pytest.mark.parametrize(
    "reason_code",
    [
        "AMOUNT_MISMATCH",
        "DESTINATION_ACCOUNT_MISMATCH",
        "DUPLICATE_PROOF",
        "UNREADABLE_PROOF",
        "INVALID_DATE",
        "UNVERIFIABLE_TRANSACTION",
        "INVALID_DOCUMENT",
        "OTHER",
    ],
)
def test_reject_accepts_every_canonical_reason_code(
    session: Session, tmp_path, reason_code
):
    base, _, _, _, attempt, admin = _graph(session)
    proof_result = _submit(session, tmp_path, attempt, base.buyer_id)
    review_payment_proof(
        session=session,
        proof_id=proof_result.proof_id,
        decision="reject",
        reviewer_user_id=admin.id,
        storage_root=tmp_path,
        reason_code=reason_code,
        reason="Motivo público para el comprador",
        notes="Nota interna opcional",
    )
    proof = session.get(PaymentProof, proof_result.proof_id)
    assert proof.rejection_reason_code == reason_code
    assert proof.rejection_reason == "Motivo público para el comprador"
    assert proof.review_notes == "Nota interna opcional"


def test_reject_rejects_unknown_reason_code(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    proof_result = _submit(session, tmp_path, attempt, base.buyer_id)
    with pytest.raises(PaymentProofServiceError, match="motivo de rechazo"):
        review_payment_proof(
            session=session,
            proof_id=proof_result.proof_id,
            decision="reject",
            reviewer_user_id=admin.id,
            storage_root=tmp_path,
            reason_code="UNKNOWN_CODE",
            reason="Motivo público",
        )
    proof = session.get(PaymentProof, proof_result.proof_id)
    assert proof.status == PaymentProofStatus.PENDING_REVIEW
    assert proof.rejection_reason_code is None


def test_approval_keeps_rejection_fields_null(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    proof_result = _submit(session, tmp_path, attempt, base.buyer_id)
    review_payment_proof(
        session=session,
        proof_id=proof_result.proof_id,
        decision="approve",
        reviewer_user_id=admin.id,
        storage_root=tmp_path,
        reason_code="OTHER",
        reason="Este texto no corresponde a una aprobación",
    )
    proof = session.get(PaymentProof, proof_result.proof_id)
    assert proof.status == PaymentProofStatus.APPROVED
    assert proof.rejection_reason_code is None
    assert proof.rejection_reason is None


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_review_replay_preserves_timestamp(session: Session, tmp_path, decision):
    base, _, _, _, attempt, admin = _graph(session)
    proof_result = _submit(session, tmp_path, attempt, base.buyer_id)
    kwargs = {"reason_code": "OTHER", "reason": "rechazo"} if decision == "reject" else {}
    review_payment_proof(session=session, proof_id=proof_result.proof_id, decision=decision, reviewer_user_id=admin.id, storage_root=tmp_path, **kwargs)
    proof = session.get(PaymentProof, proof_result.proof_id); timestamp = proof.reviewed_at
    replay = review_payment_proof(session=session, proof_id=proof.id, decision=decision, reviewer_user_id=admin.id, storage_root=tmp_path, **kwargs)
    assert replay.replayed and proof.reviewed_at == timestamp


def test_opposite_decision_is_rejected(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    review_payment_proof(session=session, proof_id=proof.proof_id, decision="approve", reviewer_user_id=admin.id, storage_root=tmp_path)
    with pytest.raises(InvalidPaymentProofTransitionError):
        review_payment_proof(session=session, proof_id=proof.proof_id, decision="reject", reviewer_user_id=admin.id, storage_root=tmp_path, reason_code="OTHER", reason="x")


def test_approve_rejects_expired_after_upload(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    with pytest.raises(PaymentProofExpiredError):
        review_payment_proof(session=session, proof_id=proof.proof_id, decision="approve", reviewer_user_id=admin.id, storage_root=tmp_path, now=attempt.expires_at + timedelta(seconds=1))


def test_review_detects_deleted_file(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    result = _submit(session, tmp_path, attempt, base.buyer_id); result.storage_path.unlink()
    with pytest.raises(PaymentProofIntegrityError):
        review_payment_proof(session=session, proof_id=result.proof_id, decision="approve", reviewer_user_id=admin.id, storage_root=tmp_path)


def test_review_detects_modified_file(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    result = _submit(session, tmp_path, attempt, base.buyer_id); result.storage_path.write_bytes(PNG + b"tampered")
    with pytest.raises(PaymentProofIntegrityError):
        review_payment_proof(session=session, proof_id=result.proof_id, decision="approve", reviewer_user_id=admin.id, storage_root=tmp_path)


@pytest.mark.parametrize(
    "role",
    [
        StaffRole.OPERATIONS_SUPERVISOR,
        StaffRole.POINT_OPERATOR,
        StaffRole.DELIVERY,
        StaffRole.TRANSPORT_OPERATOR,
        StaffRole.SUPPORT,
    ],
)
def test_service_denies_staff_without_payment_review_and_preserves_graph(
    session: Session, tmp_path, role
):
    base, order_id, _, reservation_ids, attempt, reviewer = _graph(session)
    _assign_staff_profile(session, reviewer, role=role)
    proof_result = _submit(session, tmp_path, attempt, base.buyer_id)
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == order_id)
    )

    with pytest.raises(PaymentProofServiceError, match="permiso"):
        review_payment_proof(
            session=session,
            proof_id=proof_result.proof_id,
            decision="approve",
            reviewer_user_id=reviewer.id,
            storage_root=tmp_path,
        )

    assert not user_has_permission(reviewer, "payments.review")
    assert session.get(PaymentProof, proof_result.proof_id).status == PaymentProofStatus.PENDING_REVIEW
    assert session.get(PaymentAttempt, attempt.id).status == PaymentStatus.PROCESSING
    assert session.get(Order, order_id).status == OrderStatus.PENDING_PAYMENT
    assert seller_order.status == SellerOrderStatus.PENDING_PAYMENT
    assert session.get(InventoryReservation, reservation_ids[0]).status == ReservationStatus.ACTIVE
    assert session.scalar(select(func.count(AdminAuditEvent.id))) == 0


def test_approval_audit_is_atomic_minimal_and_not_duplicated(
    session: Session, tmp_path
):
    base, _, order_number, _, attempt, admin = _graph(session)
    proof_result = _submit(session, tmp_path, attempt, base.buyer_id)
    review_payment_proof(
        session=session,
        proof_id=proof_result.proof_id,
        decision="approve",
        reviewer_user_id=admin.id,
        storage_root=tmp_path,
        notes="nota confidencial que no debe auditarse",
    )
    event = session.scalar(
        select(AdminAuditEvent).where(AdminAuditEvent.action == "PAYMENT_APPROVED")
    )
    assert event is not None
    assert event.actor_user_id == admin.id
    assert event.metadata_json["payment_public_code"] == attempt.public_code
    assert event.metadata_json["order_number"] == order_number
    assert event.metadata_json["old_status"] == PaymentStatus.PROCESSING.value
    assert event.metadata_json["new_status"] == PaymentStatus.APPROVED.value
    serialized = str(event.metadata_json).lower()
    assert "confidencial" not in serialized
    assert "sha256" not in serialized
    assert "ocr" not in serialized
    assert "account" not in serialized

    review_payment_proof(
        session=session,
        proof_id=proof_result.proof_id,
        decision="approve",
        reviewer_user_id=admin.id,
        storage_root=tmp_path,
    )
    assert session.scalar(
        select(func.count(AdminAuditEvent.id)).where(
            AdminAuditEvent.action == "PAYMENT_APPROVED"
        )
    ) == 1


def test_rejection_audit_contains_code_but_not_public_or_internal_text(
    session: Session, tmp_path
):
    base, _, order_number, _, attempt, admin = _graph(session)
    proof_result = _submit(session, tmp_path, attempt, base.buyer_id)
    review_payment_proof(
        session=session,
        proof_id=proof_result.proof_id,
        decision="reject",
        reviewer_user_id=admin.id,
        storage_root=tmp_path,
        reason_code="AMOUNT_MISMATCH",
        reason="Texto público sensible",
        notes="Texto interno sensible",
    )
    event = session.scalar(
        select(AdminAuditEvent).where(AdminAuditEvent.action == "PAYMENT_REJECTED")
    )
    assert event is not None
    assert event.actor_user_id == admin.id
    assert event.metadata_json["payment_public_code"] == attempt.public_code
    assert event.metadata_json["order_number"] == order_number
    assert event.metadata_json["reason_code"] == "AMOUNT_MISMATCH"
    serialized = str(event.metadata_json)
    assert "Texto público" not in serialized
    assert "Texto interno" not in serialized


def test_rolled_back_decision_does_not_persist_audit(session: Session, tmp_path):
    base, order_id, _, _, attempt, admin = _graph(session)
    proof_result = _submit(session, tmp_path, attempt, base.buyer_id)
    proof_id = proof_result.proof_id
    session.commit()

    review_payment_proof(
        session=session,
        proof_id=proof_id,
        decision="approve",
        reviewer_user_id=admin.id,
        storage_root=tmp_path,
    )
    assert session.scalar(select(func.count(AdminAuditEvent.id))) == 1
    session.rollback()
    session.expire_all()

    assert session.scalar(select(func.count(AdminAuditEvent.id))) == 0
    assert session.get(PaymentProof, proof_id).status == PaymentProofStatus.PENDING_REVIEW
    assert session.get(PaymentAttempt, attempt.id).status == PaymentStatus.PROCESSING
    assert session.get(Order, order_id).status == OrderStatus.PENDING_PAYMENT


def test_new_payment_attempt_gets_stable_pmt_code(session: Session):
    _, _, _, _, attempt, _ = _graph(session)
    original = attempt.public_code
    assert re.fullmatch(r"PMT-\d{8}", original)
    assert not original.startswith("PAY-")
    assert original != f"PMT-{str(attempt.id)[:8]}"
    attempt.status = PaymentStatus.PROCESSING
    session.flush()
    assert attempt.public_code == original


@pytest.mark.concurrency
def test_payment_attempt_public_code_sequence_is_concurrent_safe(
    session: Session, session_factory, concurrent_runner
):
    _, order_id, _, _, _, _ = _graph(session)
    session.commit()

    def worker(barrier):
        database_session = session_factory()
        try:
            barrier.wait()
            with database_session.begin():
                candidate = PaymentAttempt(
                    order_id=order_id,
                    method=PaymentMethod.BANK_TRANSFER,
                    status=PaymentStatus.AWAITING_PROOF,
                    amount=Decimal("20.00"),
                    currency="USD",
                    idempotency_key=f"concurrent-{uuid.uuid4().hex}",
                    request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
                )
                database_session.add(candidate)
                database_session.flush()
                return candidate.public_code
        finally:
            database_session.close()

    codes, errors = concurrent_runner([worker, worker])
    assert not errors
    assert len(codes) == len(set(codes)) == 2
    assert all(re.fullmatch(r"PMT-\d{8}", code) for code in codes)


def test_database_allows_multiple_pending_but_only_one_approved_attempt_per_order(
    session: Session,
):
    _, order_id, _, _, first, _ = _graph(session)
    second = PaymentAttempt(
        order_id=order_id,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.AWAITING_PROOF,
        amount=Decimal("20.00"),
        currency="USD",
        idempotency_key=f"pending-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    session.add(second)
    session.flush()
    assert first.id != second.id

    first.status = PaymentStatus.APPROVED
    first.approved_at = datetime.now(timezone.utc)
    session.flush()
    duplicate = PaymentAttempt(
        order_id=order_id,
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.APPROVED,
        amount=Decimal("20.00"),
        currency="USD",
        idempotency_key=f"approved-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        approved_at=datetime.now(timezone.utc),
    )
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(duplicate)
            session.flush()


def test_seller_order_transitions_with_approval(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    review_payment_proof(session=session, proof_id=proof.proof_id, decision="approve", reviewer_user_id=admin.id, storage_root=tmp_path)
    assert session.scalar(select(SellerOrder.status)) == SellerOrderStatus.CONFIRMED


def test_seller_order_transitions_with_rejection(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    review_payment_proof(session=session, proof_id=proof.proof_id, decision="reject", reviewer_user_id=admin.id, storage_root=tmp_path, reason_code="INVALID_DOCUMENT", reason="inválido")
    assert session.scalar(select(SellerOrder.status)) == SellerOrderStatus.CANCELLED


def _authorized_client(client, app, session, tmp_path):
    base, order_id, order_number, _, attempt, _ = _graph(session)
    session.commit()
    app.config["PAYMENT_PROOF_UPLOAD_DIR"] = str(tmp_path)
    with client.session_transaction() as browser_session:
        browser_session["_user_id"] = str(base.buyer_id)
        browser_session["_fresh"] = True
        browser_session["checkout_order_ids"] = [str(order_id)]
    return base, order_id, order_number, attempt


def test_transfer_page_is_private(client, app, session: Session, tmp_path):
    _, _, order_number, _ = _authorized_client(client, app, session, tmp_path)
    assert client.get(f"/checkout/transferencia/{order_number}").status_code == 200
    assert app.test_client().get(f"/checkout/transferencia/{order_number}").status_code == 302


def test_route_uploads_valid_proof(client, app, session: Session, tmp_path):
    _, _, order_number, attempt = _authorized_client(client, app, session, tmp_path)
    client.get(f"/checkout/transferencia/{order_number}")
    with client.session_transaction() as browser_session:
        token = browser_session["payment_proof_uploads"][str(attempt.id)]
    response = client.post(
        f"/checkout/transferencia/{order_number}/comprobante",
        data={"upload_token": token, "proof_file": (io.BytesIO(PNG), "proof.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302 and "/checkout/pendiente/" in response.headers["Location"]
    session.expire_all()
    proof = session.scalar(select(PaymentProof)); assert proof is not None
    assert session.get(PaymentAttempt, attempt.id).status == PaymentStatus.PROCESSING


def test_private_file_has_security_headers(client, app, session: Session, tmp_path):
    _, _, order_number, attempt = _authorized_client(client, app, session, tmp_path)
    client.get(f"/checkout/transferencia/{order_number}")
    with client.session_transaction() as browser_session:
        token = browser_session["payment_proof_uploads"][str(attempt.id)]
    client.post(f"/checkout/transferencia/{order_number}/comprobante", data={"upload_token": token, "proof_file": (io.BytesIO(PNG), "proof.png", "image/png")}, content_type="multipart/form-data")
    session.expire_all(); proof = session.scalar(select(PaymentProof))
    response = client.get(f"/pagos/comprobantes/{proof.id}/archivo")
    assert response.status_code == 200 and response.data == PNG
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_buyer_download_rejects_file_tampered_after_persistence(
    client, app, session: Session, tmp_path
):
    _, _, order_number, attempt = _authorized_client(
        client, app, session, tmp_path
    )
    client.get(f"/checkout/transferencia/{order_number}")
    with client.session_transaction() as browser_session:
        token = browser_session["payment_proof_uploads"][str(attempt.id)]
    client.post(
        f"/checkout/transferencia/{order_number}/comprobante",
        data={
            "upload_token": token,
            "proof_file": (io.BytesIO(PNG), "proof.png", "image/png"),
        },
        content_type="multipart/form-data",
    )
    session.expire_all()
    proof = session.scalar(select(PaymentProof))
    private_file_path(tmp_path, proof.storage_key).write_bytes(PNG + b"tampered")

    assert client.get(f"/pagos/comprobantes/{proof.id}/archivo").status_code == 404


def test_route_rejects_fake_signature(client, app, session: Session, tmp_path):
    _, _, order_number, attempt = _authorized_client(client, app, session, tmp_path)
    client.get(f"/checkout/transferencia/{order_number}")
    with client.session_transaction() as browser_session:
        token = browser_session["payment_proof_uploads"][str(attempt.id)]
    response = client.post(f"/checkout/transferencia/{order_number}/comprobante", data={"upload_token": token, "proof_file": (io.BytesIO(b"not png"), "proof.png", "image/png")}, content_type="multipart/form-data", follow_redirects=True)
    assert "contenido del archivo" in response.get_data(as_text=True)
    assert session.scalar(select(func.count(PaymentProof.id))) == 0


def test_pending_page_uses_honest_review_copy(client, app, session: Session, tmp_path):
    _, _, order_number, attempt = _authorized_client(client, app, session, tmp_path)
    client.get(f"/checkout/transferencia/{order_number}")
    with client.session_transaction() as browser_session:
        token = browser_session["payment_proof_uploads"][str(attempt.id)]
    client.post(f"/checkout/transferencia/{order_number}/comprobante", data={"upload_token": token, "proof_file": (io.BytesIO(PNG), "proof.png", "image/png")}, content_type="multipart/form-data")
    body = client.get(f"/checkout/pendiente/{order_number}").get_data(as_text=True)
    assert "Comprobante recibido" in body and "En revisión" in body
    assert "no implica que el pago esté aprobado" in body


def test_upload_route_requires_csrf_when_enabled(client, app, session: Session, tmp_path):
    _, _, order_number, attempt = _authorized_client(client, app, session, tmp_path)
    client.get(f"/checkout/transferencia/{order_number}")
    with client.session_transaction() as browser_session:
        token = browser_session["payment_proof_uploads"][str(attempt.id)]
    previous = app.config.get("WTF_CSRF_ENABLED")
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = client.post(
            f"/checkout/transferencia/{order_number}/comprobante",
            data={"upload_token": token, "proof_file": (io.BytesIO(PNG), "proof.png", "image/png")},
            content_type="multipart/form-data",
        )
    finally:
        app.config["WTF_CSRF_ENABLED"] = previous
    assert response.status_code == 400
    assert session.scalar(select(func.count(PaymentProof.id))) == 0


@pytest.mark.concurrency
def test_concurrent_approval_is_idempotent(
    session: Session, session_factory, concurrent_runner, tmp_path
):
    base, _, _, _, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    session.commit()

    def worker(barrier):
        database_session = session_factory()
        try:
            barrier.wait()
            with database_session.begin():
                return review_payment_proof(
                    session=database_session,
                    proof_id=proof.proof_id,
                    decision="approve",
                    reviewer_user_id=admin.id,
                    storage_root=tmp_path,
                )
        finally:
            database_session.close()

    results, errors = concurrent_runner([worker, worker])
    assert not errors and len(results) == 2
    assert sorted(result.replayed for result in results) == [False, True]


@pytest.mark.concurrency
def test_concurrent_opposite_decisions_are_atomic(
    session: Session, session_factory, concurrent_runner, tmp_path
):
    base, _, _, _, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    session.commit()

    def worker(decision):
        def execute(barrier):
            database_session = session_factory()
            try:
                barrier.wait()
                with database_session.begin():
                    return review_payment_proof(
                        session=database_session,
                        proof_id=proof.proof_id,
                        decision=decision,
                        reviewer_user_id=admin.id,
                        storage_root=tmp_path,
                        reason_code="OTHER" if decision == "reject" else None,
                        reason="rechazo concurrente" if decision == "reject" else None,
                    )
            finally:
                database_session.close()
        return execute

    results, errors = concurrent_runner([worker("approve"), worker("reject")])
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], InvalidPaymentProofTransitionError)
