from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import func, select

from app.extensions import db, limiter
from app.models import (
    AdminAuditEvent,
    InventoryBalance,
    InventoryReservation,
    Order,
    PaymentAttempt,
    PaymentProof,
    SellerOrder,
)
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentProofStatus,
    PaymentStatus,
    ReservationStatus,
    SellerOrderStatus,
    StaffEmploymentStatus,
    StaffRole,
)
from app.services.payment_proofs import PAYMENT_REJECTION_PUBLIC_REASONS
from app.services.private_storage import private_file_path
from tests.test_payment_proofs import (
    PNG,
    _assign_staff_profile,
    _graph,
    _submit,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    yield app.test_client()
    db.session.remove()


def _login(client, user) -> None:
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


def _pending_payment(session, app, tmp_path, *, expired: bool = False):
    app.config["PAYMENT_PROOF_UPLOAD_DIR"] = str(tmp_path)
    base, order_id, order_number, reservation_ids, attempt, reviewer = _graph(
        session,
        expired=False,
    )
    proof_result = _submit(
        session,
        tmp_path,
        attempt,
        base.buyer_id,
        key=f"upload-{uuid.uuid4().hex}",
    )
    if expired:
        expired_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        attempt.expires_at = expired_at
        for reservation_id in reservation_ids:
            session.get(InventoryReservation, reservation_id).expires_at = expired_at
    session.commit()
    return {
        "base": base,
        "order_id": order_id,
        "order_number": order_number,
        "reservation_ids": reservation_ids,
        "attempt_id": attempt.id,
        "payment_code": attempt.public_code,
        "proof_id": proof_result.proof_id,
        "proof_path": proof_result.storage_path,
        "reviewer": reviewer,
    }


def _audit_count(session, action: str | None = None) -> int:
    statement = select(func.count(AdminAuditEvent.id))
    if action:
        statement = statement.where(AdminAuditEvent.action == action)
    return session.scalar(statement) or 0


def _assert_pending_graph(session, graph) -> None:
    assert session.get(PaymentProof, graph["proof_id"]).status == PaymentProofStatus.PENDING_REVIEW
    assert session.get(PaymentAttempt, graph["attempt_id"]).status == PaymentStatus.PROCESSING
    assert session.get(Order, graph["order_id"]).status == OrderStatus.PENDING_PAYMENT
    assert all(
        session.get(InventoryReservation, reservation_id).status == ReservationStatus.ACTIVE
        for reservation_id in graph["reservation_ids"]
    )
    assert _audit_count(session) == 0


def test_exact_pmt_approval_is_atomic_idempotent_and_preserves_safe_return_context(
    session, client, app, tmp_path
):
    graph = _pending_payment(session, app, tmp_path)
    _login(client, graph["reviewer"])
    balance = session.get(InventoryBalance, graph["base"].balance_id)
    original_on_hand = balance.on_hand_quantity
    endpoint = f'/admin/payments/{graph["payment_code"]}/approve'

    response = client.post(
        endpoint,
        data={
            "tab": "manual_review",
            "q": "PMT",
            "method": "BANK_TRANSFER",
            "page": "2",
            "detail": "PMT-99999999",
            "next": "https://example.invalid/escape",
            "notes": "Verificación interna",
        },
    )
    assert response.status_code == 302
    location = urlparse(response.headers["Location"])
    query = parse_qs(location.query)
    assert location.path == "/admin/payments"
    assert query == {
        "tab": ["manual_review"],
        "q": ["PMT"],
        "method": ["BANK_TRANSFER"],
        "page": ["2"],
        "detail": [graph["payment_code"]],
    }

    session.expire_all()
    proof = session.get(PaymentProof, graph["proof_id"])
    attempt = session.get(PaymentAttempt, graph["attempt_id"])
    order = session.get(Order, graph["order_id"])
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == graph["order_id"])
    )
    assert proof.status == PaymentProofStatus.APPROVED
    assert proof.review_notes == "Verificación interna"
    assert attempt.status == PaymentStatus.APPROVED
    assert order.status == OrderStatus.CONFIRMED
    assert seller_order.status == SellerOrderStatus.CONFIRMED
    assert all(
        session.get(InventoryReservation, reservation_id).status == ReservationStatus.CONSUMED
        for reservation_id in graph["reservation_ids"]
    )
    assert session.get(InventoryBalance, graph["base"].balance_id).on_hand_quantity == original_on_hand
    assert _audit_count(session, "PAYMENT_APPROVED") == 1
    reviewed_at = proof.reviewed_at

    assert client.post(endpoint).status_code == 302
    session.expire_all()
    assert session.get(PaymentProof, graph["proof_id"]).reviewed_at == reviewed_at
    assert _audit_count(session, "PAYMENT_APPROVED") == 1

    opposite = client.post(
        f'/admin/payments/{graph["payment_code"]}/reject',
        data={"reason_code": "OTHER", "custom_reason": "Decisión opuesta"},
        follow_redirects=True,
    )
    assert opposite.status_code == 200
    assert "ya fue decidido por otro operador" in opposite.get_data(as_text=True)
    session.expire_all()
    assert session.get(PaymentProof, graph["proof_id"]).status == PaymentProofStatus.APPROVED
    assert _audit_count(session) == 1


def test_exact_pmt_rejection_uses_server_public_reason_and_separate_internal_notes(
    session, client, app, tmp_path
):
    graph = _pending_payment(session, app, tmp_path)
    _login(client, graph["reviewer"])

    response = client.post(
        f'/admin/payments/{graph["payment_code"]}/reject',
        data={
            "reason_code": "AMOUNT_MISMATCH",
            "custom_reason": "Texto manipulado por el navegador",
            "notes": "El OCR detectó otra cifra.",
        },
    )
    assert response.status_code == 302
    session.expire_all()
    proof = session.get(PaymentProof, graph["proof_id"])
    assert proof.status == PaymentProofStatus.REJECTED
    assert proof.rejection_reason_code == "AMOUNT_MISMATCH"
    assert proof.rejection_reason == PAYMENT_REJECTION_PUBLIC_REASONS["AMOUNT_MISMATCH"]
    assert proof.review_notes == "El OCR detectó otra cifra."
    assert session.get(PaymentAttempt, graph["attempt_id"]).status == PaymentStatus.REJECTED
    assert session.get(Order, graph["order_id"]).status == OrderStatus.CANCELLED
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == graph["order_id"])
    )
    assert seller_order.status == SellerOrderStatus.CANCELLED
    assert all(
        session.get(InventoryReservation, reservation_id).status == ReservationStatus.RELEASED
        for reservation_id in graph["reservation_ids"]
    )
    assert _audit_count(session, "PAYMENT_REJECTED") == 1
    audit = session.scalar(
        select(AdminAuditEvent).where(AdminAuditEvent.action == "PAYMENT_REJECTED")
    )
    serialized = str(audit.metadata_json)
    assert audit.metadata_json["reason_code"] == "AMOUNT_MISMATCH"
    assert proof.rejection_reason not in serialized
    assert proof.review_notes not in serialized

    terminal = client.get(f'/admin/payments?detail={graph["payment_code"]}')
    body = terminal.get_data(as_text=True)
    assert terminal.status_code == 200
    assert "Clasificación" in body
    assert "Motivo comunicado" in body
    assert "Nota interna" in body
    assert f'/admin/payments/{graph["payment_code"]}/approve' not in body
    assert f'/admin/payments/{graph["payment_code"]}/reject' not in body


def test_other_rejection_requires_and_normalizes_custom_public_reason(
    session, client, app, tmp_path
):
    graph = _pending_payment(session, app, tmp_path)
    _login(client, graph["reviewer"])
    response = client.post(
        f'/admin/payments/{graph["payment_code"]}/reject',
        data={
            "reason_code": "OTHER",
            "custom_reason": "  La referencia   no corresponde al pedido.  ",
            "notes": "Validado con tesorería.",
        },
    )
    assert response.status_code == 302
    session.expire_all()
    proof = session.get(PaymentProof, graph["proof_id"])
    assert proof.rejection_reason_code == "OTHER"
    assert proof.rejection_reason == "La referencia no corresponde al pedido."
    assert proof.review_notes == "Validado con tesorería."


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"reason_code": "UNKNOWN"},
        {"reason_code": "OTHER", "custom_reason": "   "},
        {"reason_code": "OTHER", "custom_reason": "x" * 501},
        {"reason_code": "AMOUNT_MISMATCH", "notes": "x" * 1001},
    ],
)
def test_invalid_rejection_payload_never_mutates_financial_state(
    session, client, app, tmp_path, payload
):
    graph = _pending_payment(session, app, tmp_path)
    _login(client, graph["reviewer"])
    response = client.post(
        f'/admin/payments/{graph["payment_code"]}/reject',
        data=payload,
        follow_redirects=True,
    )
    assert response.status_code == 200
    session.expire_all()
    _assert_pending_graph(session, graph)


def test_expired_or_tampered_proof_cannot_be_approved_and_exposes_safe_message(
    session, client, app, tmp_path
):
    expired = _pending_payment(session, app, tmp_path, expired=True)
    _login(client, expired["reviewer"])
    response = client.post(
        f'/admin/payments/{expired["payment_code"]}/approve',
        follow_redirects=True,
    )
    assert "reserva del pedido ya venció" in response.get_data(as_text=True)
    session.expire_all()
    _assert_pending_graph(session, expired)


def test_tampered_proof_cannot_be_approved(session, client, app, tmp_path):
    graph = _pending_payment(session, app, tmp_path)
    _login(client, graph["reviewer"])
    private_file_path(tmp_path, session.get(PaymentProof, graph["proof_id"]).storage_key).write_bytes(
        PNG + b"tampered"
    )
    response = client.post(
        f'/admin/payments/{graph["payment_code"]}/approve',
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)
    assert "integridad del comprobante" in body
    assert "tampered" not in body
    session.expire_all()
    _assert_pending_graph(session, graph)


def test_route_targets_only_the_exact_pmt_when_order_has_multiple_attempts(
    session, client, app, tmp_path
):
    graph = _pending_payment(session, app, tmp_path)
    first_proof = session.get(PaymentProof, graph["proof_id"])
    first_attempt = session.get(PaymentAttempt, graph["attempt_id"])
    second_attempt = PaymentAttempt(
        order_id=graph["order_id"],
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.AWAITING_PROOF,
        amount=Decimal("20.00"),
        currency="USD",
        idempotency_key=f"checkout-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    session.add(second_attempt)
    session.flush()
    second_proof_result = _submit(
        session,
        tmp_path,
        second_attempt,
        graph["base"].buyer_id,
        key=f"upload-{uuid.uuid4().hex}",
    )
    session.commit()
    _login(client, graph["reviewer"])

    response = client.post(
        f"/admin/payments/{second_attempt.public_code}/reject",
        data={"reason_code": "OTHER", "custom_reason": "Segundo intento rechazado."},
    )
    assert response.status_code == 302
    session.expire_all()
    assert session.get(PaymentProof, second_proof_result.proof_id).status == PaymentProofStatus.REJECTED
    assert session.get(PaymentAttempt, second_attempt.id).status == PaymentStatus.REJECTED
    assert session.get(PaymentProof, first_proof.id).status == PaymentProofStatus.PENDING_REVIEW
    assert session.get(PaymentAttempt, first_attempt.id).status == PaymentStatus.PROCESSING
    assert _audit_count(session) == 1


@pytest.mark.parametrize(
    ("role", "employment_status"),
    [
        (StaffRole.SUPPORT, StaffEmploymentStatus.ACTIVE),
        (StaffRole.SUPER_ADMIN, StaffEmploymentStatus.SUSPENDED),
    ],
)
def test_payment_decisions_require_active_payments_reviewer(
    session, client, app, tmp_path, role, employment_status
):
    graph = _pending_payment(session, app, tmp_path)
    _assign_staff_profile(
        session,
        graph["reviewer"],
        role=role,
        employment_status=employment_status,
    )
    session.commit()
    _login(client, graph["reviewer"])
    response = client.post(f'/admin/payments/{graph["payment_code"]}/approve')
    assert response.status_code == 403
    session.expire_all()
    _assert_pending_graph(session, graph)


def test_decision_routes_are_post_only_and_csrf_protected(
    session, client, app, tmp_path
):
    graph = _pending_payment(session, app, tmp_path)
    _login(client, graph["reviewer"])
    approve = f'/admin/payments/{graph["payment_code"]}/approve'
    reject = f'/admin/payments/{graph["payment_code"]}/reject'
    assert client.get(approve).status_code == 405
    assert client.get(reject).status_code == 405

    previous = app.config["WTF_CSRF_ENABLED"]
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert client.post(approve).status_code == 400
    finally:
        app.config["WTF_CSRF_ENABLED"] = previous
    session.expire_all()
    _assert_pending_graph(session, graph)


def test_exact_payment_decision_routes_register_twenty_per_hour_limit(app):
    expected_endpoints = {
        "app.blueprints.admin.approve_payment_attempt.approve_payment_attempt",
        "app.blueprints.admin.reject_payment_attempt.reject_payment_attempt",
    }
    decorated_limits = limiter.limit_manager._decorated_limits

    for endpoint in expected_endpoints:
        groups = decorated_limits.get(endpoint, [])
        assert [str(group.limit_provider) for group in groups] == ["20 per hour"]


def test_card_and_bank_transfer_without_proof_are_read_only(
    session, client, app, tmp_path
):
    graph = _pending_payment(session, app, tmp_path)
    reviewer = graph["reviewer"]
    card_attempt = PaymentAttempt(
        order_id=graph["order_id"],
        method=PaymentMethod.CARD,
        status=PaymentStatus.PROCESSING,
        amount=Decimal("20.00"),
        currency="USD",
        idempotency_key=f"card-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    bank_attempt = PaymentAttempt(
        order_id=graph["order_id"],
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.PROCESSING,
        amount=Decimal("20.00"),
        currency="USD",
        idempotency_key=f"bank-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    session.add_all([card_attempt, bank_attempt])
    session.commit()
    _login(client, reviewer)

    for attempt in (card_attempt, bank_attempt):
        response = client.post(
            f"/admin/payments/{attempt.public_code}/approve",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "no admite una decisión manual" in response.get_data(as_text=True)
        session.expire_all()
        assert session.get(PaymentAttempt, attempt.id).status == PaymentStatus.PROCESSING
    assert _audit_count(session) == 0
