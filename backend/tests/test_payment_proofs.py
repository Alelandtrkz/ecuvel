from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
    Order,
    PaymentAttempt,
    PaymentProof,
    SellerOrder,
    User,
)
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentProofStatus,
    PaymentStatus,
    ReservationStatus,
    SellerOrderStatus,
    UserStatus,
)
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
PNG = b"\x89PNG\r\n\x1a\n" + b"valid-private-proof"
JPEG = b"\xff\xd8\xff\xe0" + b"valid-private-proof"
PDF = b"%PDF-1.7\nvalid-private-proof"


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
    staged = stage_payment_proof(_upload(), root=tmp_path, max_bytes=10 * 1024 * 1024)
    return submit_bank_transfer_proof(
        session=session,
        payment_attempt_id=attempt.id,
        staged_file=staged,
        upload_idempotency_key=key,
        storage_root=tmp_path,
        uploaded_by_user_id=buyer_id,
    )


@pytest.mark.parametrize(
    ("data", "filename", "media", "expected"),
    [(PNG, "a.png", "image/png", "image/png"), (JPEG, "a.jpeg", "image/jpeg", "image/jpeg"), (PDF, "a.pdf", "application/pdf", "application/pdf")],
)
def test_stage_accepts_supported_formats(tmp_path, data, filename, media, expected):
    staged = stage_payment_proof(_upload(data, filename, media), root=tmp_path, max_bytes=1000)
    assert staged.media_type == expected
    assert staged.size_bytes == len(data)
    assert staged.temporary_path.stat().st_size == len(data)


@pytest.mark.parametrize(
    ("data", "filename", "media"),
    [(b"", "a.png", "image/png"), (PNG, "a.jpg", "image/jpeg"), (PNG, "a.png", "application/pdf"), (b"<svg/>", "a.png", "image/png"), (PNG, "a.exe", "application/octet-stream"), (PNG, "../", "image/png")],
)
def test_stage_rejects_invalid_files(tmp_path, data, filename, media):
    with pytest.raises(InvalidPaymentProofFileError):
        stage_payment_proof(_upload(data, filename, media), root=tmp_path, max_bytes=1000)
    assert not list((tmp_path / ".staging").glob("*.tmp")) if (tmp_path / ".staging").exists() else True


def test_stage_rejects_oversize_and_cleans(tmp_path):
    with pytest.raises(PaymentProofFileTooLargeError):
        stage_payment_proof(_upload(PNG + b"x" * 100), root=tmp_path, max_bytes=20)
    assert not list((tmp_path / ".staging").glob("*.tmp"))


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
    staged = stage_payment_proof(_upload(), root=tmp_path, max_bytes=1000)
    with pytest.raises(PaymentProofExpiredError):
        submit_bank_transfer_proof(session=session, payment_attempt_id=attempt.id, staged_file=staged, upload_idempotency_key="expired", storage_root=tmp_path, uploaded_by_user_id=base.buyer_id)
    delete_private_file(staged.temporary_path)


def test_upload_rejects_wrong_buyer(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    staged = stage_payment_proof(_upload(), root=tmp_path, max_bytes=1000)
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


def test_reject_releases_and_creates_movement(session: Session, tmp_path):
    base, order_id, _, reservation_ids, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    on_hand = session.get(InventoryBalance, base.balance_id).on_hand_quantity
    result = review_payment_proof(session=session, proof_id=proof.proof_id, decision="reject", reviewer_user_id=admin.id, storage_root=tmp_path, reason="No corresponde")
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


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_review_replay_preserves_timestamp(session: Session, tmp_path, decision):
    base, _, _, _, attempt, admin = _graph(session)
    proof_result = _submit(session, tmp_path, attempt, base.buyer_id)
    kwargs = {"reason": "rechazo"} if decision == "reject" else {}
    review_payment_proof(session=session, proof_id=proof_result.proof_id, decision=decision, reviewer_user_id=admin.id, storage_root=tmp_path, **kwargs)
    proof = session.get(PaymentProof, proof_result.proof_id); timestamp = proof.reviewed_at
    replay = review_payment_proof(session=session, proof_id=proof.id, decision=decision, reviewer_user_id=admin.id, storage_root=tmp_path, **kwargs)
    assert replay.replayed and proof.reviewed_at == timestamp


def test_opposite_decision_is_rejected(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    review_payment_proof(session=session, proof_id=proof.proof_id, decision="approve", reviewer_user_id=admin.id, storage_root=tmp_path)
    with pytest.raises(InvalidPaymentProofTransitionError):
        review_payment_proof(session=session, proof_id=proof.proof_id, decision="reject", reviewer_user_id=admin.id, storage_root=tmp_path, reason="x")


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


def test_seller_order_transitions_with_approval(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    review_payment_proof(session=session, proof_id=proof.proof_id, decision="approve", reviewer_user_id=admin.id, storage_root=tmp_path)
    assert session.scalar(select(SellerOrder.status)) == SellerOrderStatus.CONFIRMED


def test_seller_order_transitions_with_rejection(session: Session, tmp_path):
    base, _, _, _, attempt, admin = _graph(session)
    proof = _submit(session, tmp_path, attempt, base.buyer_id)
    review_payment_proof(session=session, proof_id=proof.proof_id, decision="reject", reviewer_user_id=admin.id, storage_root=tmp_path, reason="inválido")
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
                        reason="rechazo concurrente" if decision == "reject" else None,
                    )
            finally:
                database_session.close()
        return execute

    results, errors = concurrent_runner([worker("approve"), worker("reject")])
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], InvalidPaymentProofTransitionError)
