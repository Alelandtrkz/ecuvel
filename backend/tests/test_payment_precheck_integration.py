from __future__ import annotations

import io
import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
    Order,
    PaymentAttempt,
    PaymentProof,
    PaymentProofAnalysis,
    SellerOrder,
)
from app.models.enums import (
    OrderStatus,
    PaymentProofAnalysisStatus,
    PaymentProofPrecheckOutcome,
    PaymentStatus,
    ReservationStatus,
)
from app.services.payment_precheck import analyze_payment_proof
from app.services.payment_precheck.analyzer import _AnalysisPayload
from app.services.payment_proofs import review_payment_proof
from tests.payment_precheck_helpers import config_for, create_proof, synthetic_receipt_png
from tests.test_payment_proofs import _graph


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    test_client = app.test_client()
    yield test_client
    db.session.remove()


def _payload(outcome=PaymentProofPrecheckOutcome.PASSED):
    return _AnalysisPayload(
        processing_status=PaymentProofAnalysisStatus.COMPLETED,
        outcome=outcome,
        qr_detected=True,
        qr_decoded=True,
        findings=({"code": "SYNTHETIC", "severity": "info", "message": "Resultado sintético."},),
    )


def _analyze(session_factory, tmp_path, proof_id, monkeypatch, outcome=PaymentProofPrecheckOutcome.PASSED, force=False):
    monkeypatch.setattr(
        "app.services.payment_precheck.analyzer._run_pipeline",
        lambda *_args, **_kwargs: _payload(outcome),
    )
    return analyze_payment_proof(
        session_factory=session_factory,
        payment_proof_id=proof_id,
        config=config_for(tmp_path),
        force=force,
    )


def test_upload_creates_analysis(client, app, session, tmp_path):
    base, order_id, order_number, _, attempt, _ = _graph(session)
    session.commit()
    app.config["PAYMENT_PROOF_UPLOAD_DIR"] = str(tmp_path)
    app.config["PAYMENT_PRECHECK_ENABLED"] = True
    with client.session_transaction() as browser:
        browser["_user_id"] = str(base.buyer_id)
        browser["_fresh"] = True
        browser["checkout_order_ids"] = [str(order_id)]
    client.get(f"/checkout/transferencia/{order_number}")
    with client.session_transaction() as browser:
        token = browser["payment_proof_uploads"][str(attempt.id)]
    response = client.post(
        f"/checkout/transferencia/{order_number}/comprobante",
        data={"upload_token": token, "proof_file": (io.BytesIO(synthetic_receipt_png()), "proof.png", "image/png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    session.expire_all()
    assert session.scalar(select(func.count(PaymentProofAnalysis.id))) == 1


def test_successful_precheck_does_not_approve_payment(session, session_factory, tmp_path, monkeypatch):
    _, _, _, _, attempt_id, proof_id = create_proof(session, tmp_path); session.commit()
    _analyze(session_factory, tmp_path, proof_id, monkeypatch)
    session.expire_all()
    assert session.get(PaymentAttempt, attempt_id).status == PaymentStatus.PROCESSING
    assert session.get(PaymentProof, proof_id).status.value == "PENDING_REVIEW"


def test_failed_precheck_does_not_reject_payment(session, session_factory, tmp_path, monkeypatch):
    _, _, _, _, attempt_id, proof_id = create_proof(session, tmp_path); session.commit()
    _analyze(session_factory, tmp_path, proof_id, monkeypatch, PaymentProofPrecheckOutcome.FAILED)
    session.expire_all()
    assert session.get(PaymentAttempt, attempt_id).status == PaymentStatus.PROCESSING


def test_precheck_does_not_consume_reservations(session, session_factory, tmp_path, monkeypatch):
    _, _, _, reservations, _, proof_id = create_proof(session, tmp_path); session.commit()
    _analyze(session_factory, tmp_path, proof_id, monkeypatch)
    session.expire_all()
    assert session.get(InventoryReservation, reservations[0]).status == ReservationStatus.ACTIVE


def test_precheck_does_not_change_inventory_or_movements(session, session_factory, tmp_path, monkeypatch):
    base, _, _, _, _, proof_id = create_proof(session, tmp_path); session.commit()
    balance = session.get(InventoryBalance, base.balance_id)
    before = (balance.on_hand_quantity, balance.reserved_quantity, balance.blocked_quantity, session.scalar(select(func.count(InventoryMovement.id))))
    _analyze(session_factory, tmp_path, proof_id, monkeypatch)
    session.expire_all(); balance = session.get(InventoryBalance, base.balance_id)
    after = (balance.on_hand_quantity, balance.reserved_quantity, balance.blocked_quantity, session.scalar(select(func.count(InventoryMovement.id))))
    assert after == before


def test_precheck_does_not_change_order_statuses(session, session_factory, tmp_path, monkeypatch):
    _, order_id, _, _, _, proof_id = create_proof(session, tmp_path); session.commit()
    _analyze(session_factory, tmp_path, proof_id, monkeypatch)
    session.expire_all()
    assert session.get(Order, order_id).status == OrderStatus.PENDING_PAYMENT
    assert session.scalar(select(SellerOrder.status)).value == "PENDING_PAYMENT"


def test_manual_approval_still_works_after_passed_precheck(session, session_factory, tmp_path, monkeypatch):
    base, order_id, _, reservations, _, proof_id = create_proof(session, tmp_path); session.commit()
    _analyze(session_factory, tmp_path, proof_id, monkeypatch)
    with session.begin():
        review_payment_proof(session=session, proof_id=proof_id, decision="approve", reviewer_user_id=base.operator_id, storage_root=tmp_path)
    assert session.get(Order, order_id).status == OrderStatus.CONFIRMED
    assert session.get(InventoryReservation, reservations[0]).status == ReservationStatus.CONSUMED


def test_manual_rejection_still_works_after_failed_precheck(session, session_factory, tmp_path, monkeypatch):
    base, order_id, _, reservations, _, proof_id = create_proof(session, tmp_path); session.commit()
    _analyze(session_factory, tmp_path, proof_id, monkeypatch, PaymentProofPrecheckOutcome.FAILED)
    with session.begin():
        review_payment_proof(session=session, proof_id=proof_id, decision="reject", reviewer_user_id=base.operator_id, storage_root=tmp_path, reason="No corresponde")
    assert session.get(Order, order_id).status == OrderStatus.CANCELLED
    assert session.get(InventoryReservation, reservations[0]).status == ReservationStatus.RELEASED


def test_pdf_creates_manual_review_outcome(session, session_factory, tmp_path):
    _, _, _, _, _, proof_id = create_proof(session, tmp_path, data=b"%PDF-1.7\nsynthetic", filename="proof.pdf", media_type="application/pdf"); session.commit()
    result = analyze_payment_proof(session_factory=session_factory, payment_proof_id=proof_id, config=config_for(tmp_path))
    assert result.processing_status == PaymentProofAnalysisStatus.COMPLETED
    assert result.outcome == PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW


def test_repeat_analysis_without_force_is_idempotent(session, session_factory, tmp_path, monkeypatch):
    _, _, _, _, _, proof_id = create_proof(session, tmp_path); session.commit()
    first = _analyze(session_factory, tmp_path, proof_id, monkeypatch)
    second = analyze_payment_proof(session_factory=session_factory, payment_proof_id=proof_id, config=config_for(tmp_path))
    assert second.analysis_id == first.analysis_id and second.replayed
    assert second.run_count == 1


def test_force_reanalysis_updates_same_record(session, session_factory, tmp_path, monkeypatch):
    _, _, _, _, _, proof_id = create_proof(session, tmp_path); session.commit()
    first = _analyze(session_factory, tmp_path, proof_id, monkeypatch)
    second = _analyze(session_factory, tmp_path, proof_id, monkeypatch, force=True)
    assert second.analysis_id == first.analysis_id and second.run_count == 2


@pytest.mark.concurrency
def test_concurrent_analysis_creates_single_result(session, session_factory, concurrent_runner, tmp_path, monkeypatch):
    _, _, _, _, _, proof_id = create_proof(session, tmp_path); session.commit()
    def slow_pipeline(*_args, **_kwargs):
        time.sleep(0.4)
        return _payload()
    monkeypatch.setattr("app.services.payment_precheck.analyzer._run_pipeline", slow_pipeline)
    def worker(barrier):
        barrier.wait()
        return analyze_payment_proof(session_factory=session_factory, payment_proof_id=proof_id, config=config_for(tmp_path))
    results, errors = concurrent_runner([worker, worker])
    assert not errors and len(results) == 2
    assert sum(result.executed for result in results) == 1
    session.expire_all()
    assert session.scalar(select(func.count(PaymentProofAnalysis.id))) == 1
