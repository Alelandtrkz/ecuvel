from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import (
    Order,
    PaymentAttempt,
    PaymentProof,
    PaymentProofAnalysis,
    User,
)
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentProofAnalysisStatus,
    PaymentProofPrecheckOutcome,
    PaymentProofStatus,
    PaymentStatus,
    UserStatus,
)
from app.services.admin_payments import (
    AdminPaymentNotFoundError,
    AdminPaymentQueryError,
    build_payment_timeline,
    ecuador_day_utc_bounds,
    get_admin_payment_detail,
    get_admin_payment_kpis,
    get_order_payment_attempt_history,
    list_admin_payments,
)


pytestmark = pytest.mark.integration


def _buyer(session: Session, *, name: str = "María Cliente") -> User:
    token = uuid.uuid4().hex[:10]
    buyer = User(
        public_code=f"U-{token}",
        email=f"maria-{token}@ecuvel.test",
        email_normalized=f"maria-{token}@ecuvel.test",
        password_hash="test",
        full_name=name,
        status=UserStatus.ACTIVE,
    )
    session.add(buyer)
    session.flush()
    return buyer


def _staff(session: Session) -> User:
    token = uuid.uuid4().hex[:10]
    staff = User(
        public_code=f"ADM-{token}",
        email=f"admin-{token}@ecuvel.test",
        email_normalized=f"admin-{token}@ecuvel.test",
        password_hash="test",
        full_name="Admin Financiero",
        status=UserStatus.ACTIVE,
        is_ecuvel_staff=True,
    )
    session.add(staff)
    session.flush()
    return staff


def _order(
    session: Session,
    buyer: User,
    *,
    amount: Decimal,
    status: OrderStatus = OrderStatus.PENDING_PAYMENT,
    number: str | None = None,
) -> Order:
    token = uuid.uuid4().hex[:10]
    order = Order(
        order_number=number or f"ECV-{token}",
        buyer_id=buyer.id,
        status=status,
        currency="USD",
        subtotal=amount,
        discount_total=Decimal("0.00"),
        shipping_total=Decimal("0.00"),
        tax_total=Decimal("0.00"),
        grand_total=amount,
    )
    session.add(order)
    session.flush()
    return order


def _attempt(
    session: Session,
    order: Order,
    *,
    code: str,
    status: PaymentStatus,
    amount: Decimal | None = None,
    method: PaymentMethod = PaymentMethod.BANK_TRANSFER,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    approved_at: datetime | None = None,
    rejected_at: datetime | None = None,
    failed_at: datetime | None = None,
    provider_reference: str | None = None,
) -> PaymentAttempt:
    created = created_at or datetime.now(timezone.utc)
    attempt = PaymentAttempt(
        public_code=code,
        order_id=order.id,
        method=method,
        status=status,
        amount=amount if amount is not None else order.grand_total,
        currency=order.currency,
        idempotency_key=f"idem-{uuid.uuid4().hex}",
        request_fingerprint=uuid.uuid4().hex.ljust(64, "0"),
        provider="test-provider" if provider_reference else None,
        provider_reference=provider_reference,
        expires_at=expires_at or created + timedelta(hours=1),
        approved_at=approved_at,
        rejected_at=rejected_at,
        failed_at=failed_at,
        created_at=created,
        updated_at=created,
    )
    session.add(attempt)
    session.flush()
    return attempt


def _proof(
    session: Session,
    attempt: PaymentAttempt,
    *,
    status: PaymentProofStatus = PaymentProofStatus.PENDING_REVIEW,
    created_at: datetime | None = None,
    reviewer: User | None = None,
    reviewed_at: datetime | None = None,
) -> PaymentProof:
    token = uuid.uuid4().hex
    proof = PaymentProof(
        payment_attempt_id=attempt.id,
        storage_key=f"private/{token}.pdf",
        original_filename="../comprobante.pdf",
        media_type="application/pdf",
        size_bytes=123,
        sha256="a" * 64,
        status=status,
        upload_idempotency_key=f"upload-{token}",
        reviewed_by_user_id=reviewer.id if reviewer else None,
        reviewed_at=reviewed_at,
        rejection_reason_code=("AMOUNT_MISMATCH" if status == PaymentProofStatus.REJECTED else None),
        rejection_reason=("El monto no coincide." if status == PaymentProofStatus.REJECTED else None),
        review_notes="Nota financiera privada.",
        created_at=created_at or attempt.created_at + timedelta(minutes=1),
        updated_at=reviewed_at or created_at or attempt.created_at + timedelta(minutes=1),
    )
    session.add(proof)
    session.flush()
    return proof


def _analysis(
    session: Session,
    proof: PaymentProof,
    *,
    outcome: PaymentProofPrecheckOutcome | None,
    processing_status: PaymentProofAnalysisStatus = PaymentProofAnalysisStatus.COMPLETED,
    receipt: str = "REC-123",
    reference: str = "TRX-987",
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> PaymentProofAnalysis:
    started = started_at or proof.created_at + timedelta(minutes=1)
    analysis = PaymentProofAnalysis(
        payment_proof_id=proof.id,
        processing_status=processing_status,
        outcome=outcome,
        analyzer_version="test-v1",
        bank_name_detected="Banco Test",
        amount_detected=proof.payment_attempt.amount,
        transaction_at_detected=proof.created_at,
        destination_account_suffix="2608",
        receipt_number_detected=receipt,
        transaction_reference_detected=reference,
        ocr_mean_confidence=Decimal("91.50"),
        ocr_word_count=25,
        amount_matches=True,
        destination_account_matches=True,
        date_is_plausible=True,
        receipt_appears_unique=True,
        bank_is_recognized=True,
        findings=[
            {"code": "AMOUNT_MATCH", "severity": "info", "message": "Monto correcto."},
            {"code": "UNTRUSTED_HTML", "severity": "error", "message": "<script>x</script>"},
        ],
        qr_payload_sha256="secret-qr-hash",
        started_at=started,
        completed_at=completed_at or started + timedelta(minutes=1),
        created_at=started,
        updated_at=completed_at or started + timedelta(minutes=1),
    )
    session.add(analysis)
    session.flush()
    return analysis


def test_ecuador_bounds_and_kpis_use_local_day(session: Session):
    now = datetime(2026, 8, 26, 4, 30, tzinfo=timezone.utc)
    start, end = ecuador_day_utc_bounds(now=now)
    assert start == datetime(2026, 8, 25, 5, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 26, 5, 0, tzinfo=timezone.utc)

    buyer = _buyer(session)
    included_order = _order(session, buyer, amount=Decimal("45.00"), status=OrderStatus.CONFIRMED)
    excluded_order = _order(session, buyer, amount=Decimal("99.00"), status=OrderStatus.CONFIRMED)
    _attempt(
        session,
        included_order,
        code="PMT-00000001",
        status=PaymentStatus.APPROVED,
        approved_at=start + timedelta(minutes=1),
    )
    _attempt(
        session,
        excluded_order,
        code="PMT-00000002",
        status=PaymentStatus.APPROVED,
        approved_at=start - timedelta(minutes=1),
    )

    review_order = _order(session, buyer, amount=Decimal("20.00"))
    review = _attempt(session, review_order, code="PMT-00000003", status=PaymentStatus.PROCESSING)
    review_proof = _proof(session, review)
    _analysis(session, review_proof, outcome=PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW)

    awaiting_order = _order(session, buyer, amount=Decimal("12.00"))
    _attempt(
        session,
        awaiting_order,
        code="PMT-00000004",
        status=PaymentStatus.AWAITING_PROOF,
        expires_at=now + timedelta(minutes=20),
    )
    expired_awaiting_order = _order(session, buyer, amount=Decimal("15.00"))
    _attempt(
        session,
        expired_awaiting_order,
        code="PMT-00000005",
        status=PaymentStatus.AWAITING_PROOF,
        expires_at=now - timedelta(minutes=1),
    )

    for index, (status, amount) in enumerate((
        (PaymentStatus.REJECTED, Decimal("7.00")),
        (PaymentStatus.FAILED, Decimal("8.00")),
        (PaymentStatus.CANCELLED, Decimal("9.00")),
        (PaymentStatus.EXPIRED, Decimal("10.00")),
    ), start=6):
        order = _order(session, buyer, amount=amount)
        terminal = start + timedelta(hours=1)
        _attempt(
            session,
            order,
            code=f"PMT-{index:08d}",
            status=status,
            rejected_at=terminal if status == PaymentStatus.REJECTED else None,
            failed_at=terminal if status != PaymentStatus.REJECTED else None,
        )
    session.flush()

    result = get_admin_payment_kpis(session, now=now)
    assert result.collected_today_amount == Decimal("45.00")
    assert result.collected_today_count == 1
    assert result.manual_review_amount == Decimal("20.00")
    assert result.manual_review_count == 1
    assert result.manual_review_flagged_count == 1
    assert result.awaiting_proof_amount == Decimal("12.00")
    assert result.awaiting_proof_count == 1
    assert result.failed_rejected_today_amount == Decimal("15.00")
    assert result.failed_rejected_today_count == 2


def test_empty_kpis_return_zero_decimals_and_counts(session: Session):
    result = get_admin_payment_kpis(
        session,
        now=datetime(2026, 8, 26, 12, tzinfo=timezone.utc),
    )
    assert result.collected_today_amount == Decimal("0.00")
    assert result.manual_review_amount == Decimal("0.00")
    assert result.awaiting_proof_amount == Decimal("0.00")
    assert result.failed_rejected_today_amount == Decimal("0.00")
    assert result.collected_today_count == 0
    assert result.manual_review_count == 0
    assert result.awaiting_proof_count == 0
    assert result.failed_rejected_today_count == 0
    assert result.manual_review_oldest_submitted_at is None
    assert result.awaiting_proof_next_expiration_at is None


def test_list_is_one_row_per_attempt_and_has_stable_pagination(session: Session):
    buyer = _buyer(session)
    order = _order(session, buyer, amount=Decimal("30.00"))
    base = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
    for index in range(5):
        _attempt(
            session,
            order,
            code=f"PMT-{index + 1:08d}",
            status=PaymentStatus.REJECTED,
            amount=Decimal("30.00"),
            created_at=base + timedelta(minutes=index),
            rejected_at=base + timedelta(minutes=index, seconds=30),
        )

    first = list_admin_payments(session, page=1, per_page=2)
    second = list_admin_payments(session, page=2, per_page=2)
    assert first.total == 5
    assert first.pages == 3
    assert first.has_next and not first.has_prev
    assert second.has_next and second.has_prev
    assert [item.payment_public_code for item in first.items] == [
        "PMT-00000005",
        "PMT-00000004",
    ]
    assert not ({item.payment_public_code for item in first.items} & {
        item.payment_public_code for item in second.items
    })


@pytest.mark.parametrize("field", ["payment", "order", "user", "name", "email", "receipt", "reference"])
def test_search_supports_safe_identifiers_and_text(session: Session, field: str):
    buyer = _buyer(session, name="María Fernanda Única")
    order = _order(session, buyer, amount=Decimal("22.00"), number="ECV-SEARCH-001")
    attempt = _attempt(
        session,
        order,
        code="PMT-00000123",
        status=PaymentStatus.PROCESSING,
        provider_reference="PROVIDER-ABC",
    )
    proof = _proof(session, attempt)
    _analysis(
        session,
        proof,
        outcome=PaymentProofPrecheckOutcome.PASSED,
        receipt="REC-SEARCH-42",
        reference="TRX-SEARCH-42",
    )
    queries = {
        "payment": attempt.public_code,
        "order": order.order_number,
        "user": buyer.public_code,
        "name": "Fernanda Única",
        "email": buyer.email.split("@")[0],
        "receipt": "REC-SEARCH-42",
        "reference": "TRX-SEARCH-42",
    }
    result = list_admin_payments(session, query=queries[field])
    assert [item.payment_public_code for item in result.items] == [attempt.public_code]
    assert list_admin_payments(session, query="sin-coincidencia").total == 0


def test_tabs_and_filters_are_composable_and_validated(session: Session):
    buyer = _buyer(session)
    created = datetime(2026, 8, 25, 6, tzinfo=timezone.utc)
    target_order = _order(session, buyer, amount=Decimal("120.00"))
    target = _attempt(
        session,
        target_order,
        code="PMT-00000011",
        status=PaymentStatus.PROCESSING,
        created_at=created,
    )
    proof = _proof(session, target)
    _analysis(session, proof, outcome=PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW)

    other_order = _order(session, buyer, amount=Decimal("50.00"))
    _attempt(
        session,
        other_order,
        code="PMT-00000012",
        status=PaymentStatus.CANCELLED,
        method=PaymentMethod.CARD,
        created_at=created,
        failed_at=created + timedelta(minutes=2),
    )
    result = list_admin_payments(
        session,
        tab="manual_review",
        method="BANK_TRANSFER",
        status="PROCESSING",
        date_from=date(2026, 8, 25),
        date_to="2026-08-25",
        amount_min="100.00",
        amount_max=Decimal("150.00"),
        analysis="NEEDS_MANUAL_REVIEW",
    )
    assert [item.payment_public_code for item in result.items] == [target.public_code]
    assert list_admin_payments(session, status="CANCELLED").total == 1
    assert list_admin_payments(session, analysis="NO_ANALYSIS").total == 1
    with pytest.raises(AdminPaymentQueryError):
        list_admin_payments(session, status="UNKNOWN")
    with pytest.raises(AdminPaymentQueryError):
        list_admin_payments(session, amount_min="10", amount_max="1")
    with pytest.raises(AdminPaymentQueryError):
        list_admin_payments(session, page=0)
    with pytest.raises(AdminPaymentQueryError):
        list_admin_payments(session, per_page=101)


def test_row_view_uses_permissions_and_real_verification_state(session: Session):
    buyer = _buyer(session)
    staff = _staff(session)
    order = _order(session, buyer, amount=Decimal("44.00"))
    attempt = _attempt(session, order, code="PMT-00000021", status=PaymentStatus.PROCESSING)
    proof = _proof(session, attempt)
    _analysis(session, proof, outcome=PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW)

    item = list_admin_payments(session, current_user=staff).items[0]
    assert item.customer.public_code == buyer.public_code
    assert item.status_label_es == "En revisión"
    assert item.verification_summary == "Requiere revisión manual"
    assert item.can_review is True
    assert list_admin_payments(session).items[0].can_review is False


def test_detail_is_safe_and_timeline_uses_only_real_timestamps(session: Session):
    buyer = _buyer(session)
    staff = _staff(session)
    order = _order(session, buyer, amount=Decimal("45.00"), status=OrderStatus.CONFIRMED)
    created = datetime(2026, 8, 25, 20, tzinfo=timezone.utc)
    reviewed = created + timedelta(minutes=4)
    attempt = _attempt(
        session,
        order,
        code="PMT-00000031",
        status=PaymentStatus.APPROVED,
        created_at=created,
        approved_at=reviewed,
        provider_reference="SAFE-REFERENCE",
    )
    proof = _proof(
        session,
        attempt,
        status=PaymentProofStatus.APPROVED,
        reviewer=staff,
        reviewed_at=reviewed,
    )
    _analysis(
        session,
        proof,
        outcome=PaymentProofPrecheckOutcome.PASSED,
        started_at=created + timedelta(minutes=2),
        completed_at=created + timedelta(minutes=3),
    )
    detail = get_admin_payment_detail(session, attempt.public_code, current_user=staff)
    assert detail.proof.original_filename == "comprobante.pdf"
    assert detail.analysis.outcome == "PASSED"
    assert detail.analysis.findings[-1].code == "UNRECOGNIZED_FINDING"
    assert [entry.timestamp for entry in detail.timeline] == sorted(
        entry.timestamp for entry in detail.timeline
    )
    assert next(entry for entry in detail.timeline if entry.key == "awaiting_proof").is_derived
    assert detail.can_review is False
    payload = asdict(detail)
    rendered = repr(payload).lower()
    for forbidden in ("storage_key", "sha256", "filesystem", "qr_payload", "raw_ocr"):
        assert forbidden not in rendered
    assert "secret-qr-hash" not in rendered
    with pytest.raises(AdminPaymentNotFoundError):
        get_admin_payment_detail(session, "PMT-99999999")


def test_timeline_uses_semantic_order_when_events_share_timestamp(session: Session):
    buyer = _buyer(session)
    order = _order(session, buyer, amount=Decimal("11.00"))
    created = datetime(2026, 8, 25, 20, tzinfo=timezone.utc)
    attempt = _attempt(
        session,
        order,
        code="PMT-00000040",
        status=PaymentStatus.AWAITING_PROOF,
        created_at=created,
    )

    timeline = build_payment_timeline(attempt, None, None)

    assert [entry.key for entry in timeline] == [
        "payment_started",
        "awaiting_proof",
    ]
    assert timeline[0].timestamp == timeline[1].timestamp == created


def test_failed_precheck_timeline_does_not_claim_completion(session: Session):
    buyer = _buyer(session)
    order = _order(session, buyer, amount=Decimal("11.00"))
    attempt = _attempt(session, order, code="PMT-00000041", status=PaymentStatus.PROCESSING)
    proof = _proof(session, attempt)
    analysis = _analysis(
        session,
        proof,
        outcome=PaymentProofPrecheckOutcome.FAILED,
        processing_status=PaymentProofAnalysisStatus.FAILED,
    )
    keys = [entry.key for entry in build_payment_timeline(attempt, proof, analysis)]
    assert "precheck_failed" in keys
    assert "precheck_completed" not in keys


@pytest.mark.parametrize(
    ("status", "terminal_field", "timeline_key"),
    [
        (PaymentStatus.REJECTED, "rejected_at", "rejected"),
        (PaymentStatus.EXPIRED, "failed_at", "expired"),
    ],
)
def test_detail_without_analysis_supports_terminal_states(
    session: Session,
    status: PaymentStatus,
    terminal_field: str,
    timeline_key: str,
):
    buyer = _buyer(session)
    order = _order(session, buyer, amount=Decimal("18.00"))
    created = datetime(2026, 8, 25, 15, tzinfo=timezone.utc)
    kwargs = {terminal_field: created + timedelta(minutes=5)}
    attempt = _attempt(
        session,
        order,
        code=(
            "PMT-00000042"
            if status == PaymentStatus.REJECTED
            else "PMT-00000043"
        ),
        status=status,
        created_at=created,
        **kwargs,
    )
    proof = None
    if status == PaymentStatus.REJECTED:
        proof = _proof(
            session,
            attempt,
            status=PaymentProofStatus.REJECTED,
            reviewed_at=kwargs[terminal_field],
        )

    detail = get_admin_payment_detail(session, attempt.public_code)
    assert (detail.proof is not None) is (proof is not None)
    assert detail.analysis is None
    assert timeline_key in {entry.key for entry in detail.timeline}


def test_detail_without_proof_is_valid(session: Session):
    buyer = _buyer(session)
    order = _order(session, buyer, amount=Decimal("13.00"))
    attempt = _attempt(
        session,
        order,
        code="PMT-00000044",
        status=PaymentStatus.AWAITING_PROOF,
    )
    detail = get_admin_payment_detail(session, attempt.public_code)
    assert detail.proof is None
    assert detail.analysis is None
    assert detail.can_review is False
    assert {entry.key for entry in detail.timeline} == {
        "payment_started",
        "awaiting_proof",
    }


def test_attempt_history_includes_terminal_attempts_and_marks_canonical_current(session: Session):
    buyer = _buyer(session)
    order = _order(session, buyer, amount=Decimal("25.00"), status=OrderStatus.CONFIRMED)
    base = datetime(2026, 8, 25, 10, tzinfo=timezone.utc)
    expired = _attempt(
        session,
        order,
        code="PMT-00000051",
        status=PaymentStatus.EXPIRED,
        created_at=base,
        failed_at=base + timedelta(minutes=10),
    )
    rejected = _attempt(
        session,
        order,
        code="PMT-00000052",
        status=PaymentStatus.REJECTED,
        created_at=base + timedelta(hours=1),
        rejected_at=base + timedelta(hours=1, minutes=5),
    )
    approved = _attempt(
        session,
        order,
        code="PMT-00000053",
        status=PaymentStatus.APPROVED,
        created_at=base + timedelta(hours=2),
        approved_at=base + timedelta(hours=2, minutes=5),
    )
    history = get_order_payment_attempt_history(session, order.id)
    assert {item.payment_public_code for item in history} == {
        expired.public_code,
        rejected.public_code,
        approved.public_code,
    }
    assert next(item for item in history if item.is_current).payment_public_code == approved.public_code


def test_list_query_count_does_not_grow_with_page_size(session: Session):
    buyer = _buyer(session)
    for index in range(20):
        order = _order(session, buyer, amount=Decimal("10.00"))
        attempt = _attempt(
            session,
            order,
            code=f"PMT-{index + 100:08d}",
            status=PaymentStatus.PROCESSING,
        )
        _proof(session, attempt)
    session.flush()
    engine = session.get_bind()
    statements: list[str] = []

    def count_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        list_admin_payments(session, page=1, per_page=5)
        count_for_five = len(statements)
        statements.clear()
        list_admin_payments(session, page=1, per_page=20)
        count_for_twenty = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)
    assert count_for_five == count_for_twenty == 2
