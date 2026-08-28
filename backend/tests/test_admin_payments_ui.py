from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import AdminAuditEvent, PaymentAttempt, PaymentProof, StaffProfile, User
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentProofPrecheckOutcome,
    PaymentProofStatus,
    PaymentStatus,
    StaffEmploymentStatus,
    StaffIdentificationType,
    StaffRole,
)
from tests.test_admin_payments import (
    _analysis,
    _attempt,
    _buyer,
    _order,
    _proof,
    _staff,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    yield app.test_client()
    db.session.remove()


def _login(client, user: User) -> None:
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


def _role_staff(session, role: StaffRole) -> User:
    user = _staff(session)
    session.add(StaffProfile(
        user=user,
        identification_type=StaffIdentificationType.OTHER,
        identification_number_normalized=f"PAY-{uuid.uuid4().hex[:16]}",
        nationality_code="ECU",
        role=role,
        employment_status=StaffEmploymentStatus.ACTIVE,
        employment_started_at=date.today(),
    ))
    session.flush()
    return user


def _payment_graph(session, *, code: str, status: PaymentStatus, amount: str = "145.50",
                   method: PaymentMethod = PaymentMethod.BANK_TRANSFER,
                   buyer_name: str = "Cliente de pagos"):
    buyer = _buyer(session, name=buyer_name)
    order = _order(
        session,
        buyer,
        amount=Decimal(amount),
        status=OrderStatus.CONFIRMED if status == PaymentStatus.APPROVED else OrderStatus.PENDING_PAYMENT,
    )
    attempt = _attempt(
        session,
        order,
        code=code,
        status=status,
        amount=Decimal(amount),
        method=method,
        approved_at=(datetime.now(timezone.utc) if status == PaymentStatus.APPROVED else None),
    )
    return buyer, order, attempt


def test_admin_payments_navigation_and_route_apply_payments_view(session, client):
    legacy_admin = _staff(session)
    support = _role_staff(session, StaffRole.SUPPORT)
    buyer = _buyer(session)
    session.commit()

    assert client.get("/admin/payments").status_code == 302

    _login(client, buyer)
    assert client.get("/admin/payments").status_code == 403

    _login(client, support)
    forbidden = client.get("/admin/payments")
    assert forbidden.status_code == 403
    support_shell = client.get("/admin/reviews").get_data(as_text=True)
    assert 'href="/admin/payments"' not in support_shell

    _login(client, legacy_admin)
    response = client.get("/admin/payments")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Pagos" in body
    assert 'href="/admin/payments"' in body
    assert "Liquidaciones" in body


def test_admin_payments_renders_real_rows_kpis_filters_and_no_stitch_mocks(session, client):
    staff = _staff(session)
    buyer, order, review_attempt = _payment_graph(
        session,
        code="PMT-00000124",
        status=PaymentStatus.PROCESSING,
        buyer_name="Cliente real del pago",
    )
    proof = _proof(session, review_attempt)
    _analysis(session, proof, outcome=PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW)
    _other_buyer, approved_order, approved_attempt = _payment_graph(
        session,
        code="PMT-00000125",
        status=PaymentStatus.APPROVED,
        amount="89.90",
        method=PaymentMethod.CARD,
        buyer_name="Compradora de tarjeta",
    )
    session.commit()
    _login(client, staff)

    response = client.get("/admin/payments")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert review_attempt.public_code in body
    assert approved_attempt.public_code in body
    assert order.order_number in body
    assert approved_order.order_number in body
    assert buyer.full_name in body
    assert "$145.50" in body or "$145,50" in body
    assert "En revisi" in body
    assert "Tarjeta" in body

    for url in (
        "/admin/payments?tab=manual_review",
        "/admin/payments?method=BANK_TRANSFER",
        "/admin/payments?status=APPROVED",
        f"/admin/payments?q={review_attempt.public_code}",
        "/admin/payments?amount_min=100&amount_max=200",
        "/admin/payments?analysis=NEEDS_MANUAL_REVIEW",
    ):
        assert client.get(url).status_code == 200

    invalid = client.get("/admin/payments?status=NOT_A_STATUS&amount_min=oops")
    assert invalid.status_code == 302
    assert invalid.headers["Location"].endswith("/admin/payments?tab=all")

    forbidden_literals = (
        "Stripe Payments",
        "VISA 4242",
        "TRX-492810",
        "AI Assessed",
        "Inteligencia Artificial",
        "Nuevo Registro",
        "Exportar",
        "$12.450,80",
    )
    for literal in forbidden_literals:
        assert literal not in body


def test_advanced_filters_start_closed_and_open_when_any_advanced_filter_is_active(
    session, client, app
):
    staff = _staff(session)
    session.commit()
    _login(client, staff)

    default_body = client.get("/admin/payments").get_data(as_text=True)
    assert "Todos los métodos" in default_body
    assert 'data-payment-advanced-trigger aria-expanded="false"' in default_body
    assert 'data-payment-advanced-panel hidden' in default_body

    for query in (
        "date_from=2026-08-01",
        "date_to=2026-08-31",
        "amount_min=1.00",
        "amount_max=100.00",
        "analysis=NO_ANALYSIS",
    ):
        body = client.get(f"/admin/payments?{query}").get_data(as_text=True)
        assert 'data-payment-advanced-trigger aria-expanded="true"' in body
        assert 'data-payment-advanced-panel hidden' not in body

    script = (Path(app.static_folder) / "js" / "admin-payments.js").read_text(
        encoding="utf-8"
    )
    assert 'advancedTrigger.addEventListener("click"' in script
    assert 'advancedTrigger.setAttribute("aria-expanded", String(!expanded))' in script
    assert "advancedPanel.hidden = expanded" in script


def test_payment_drawer_is_exact_safe_reviewable_and_preserves_context(session, client, app):
    staff = _staff(session)
    _buyer_one, order, attempt = _payment_graph(
        session, code="PMT-00000010", status=PaymentStatus.PROCESSING
    )
    proof = _proof(session, attempt)
    _analysis(session, proof, outcome=PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW)
    _buyer_two, _other_order, other_attempt = _payment_graph(
        session, code="PMT-00000011", status=PaymentStatus.REJECTED, amount="12.00"
    )
    _proof(session, other_attempt, status=PaymentProofStatus.REJECTED, reviewer=staff,
           reviewed_at=datetime.now(timezone.utc))
    session.commit()
    _login(client, staff)

    url = (
        "/admin/payments?tab=manual_review&q=PMT&method=BANK_TRANSFER&page=1"
        "&detail=PMT-00000010"
    )
    response = client.get(url)
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'role="dialog"' in body
    assert attempt.public_code in body
    assert order.order_number in body
    assert "comprobante.pdf" in body
    assert "TRX-987" in body
    assert "Monto correcto." in body
    assert "<script>x</script>" not in body
    assert "secret-qr-hash" not in body
    assert proof.storage_key not in body
    assert proof.sha256 not in body
    assert "Aprobar pago" in body
    assert "Rechazar pago" in body
    assert "Confirmar aprobación" in body
    assert "Confirma que verificaste el comprobante" in body
    assert f'action="/admin/payments/{attempt.public_code}/approve"' in body
    assert f'action="/admin/payments/{attempt.public_code}/reject"' in body
    assert 'name="tab" value="manual_review"' in body
    assert 'name="q" value="PMT"' in body
    assert 'name="method" value="BANK_TRANSFER"' in body
    assert 'name="detail"' not in body
    assert 'name="next"' not in body
    assert 'aria-haspopup="dialog"' in body
    assert 'aria-expanded="false"' in body
    assert 'class="admin-payment-customer"' in body
    assert f"<span>{_buyer_one.full_name}</span>" in body
    assert f"<small>{_buyer_one.public_code}</small>" in body
    assert "tab=manual_review" in body
    assert "q=PMT" in body
    assert "method=BANK_TRANSFER" in body
    assert "detail=PMT-00000010" not in body.split("data-payment-drawer-close", 1)[0][-500:]

    script = (Path(app.static_folder) / "js" / "admin-payments.js").read_text(
        encoding="utf-8"
    )
    assert 'opener.setAttribute("aria-expanded", "true")' in script
    assert 'button.setAttribute("aria-expanded", "false")' in script
    assert 'if (activeModalLayer)' in script
    assert 'event.key === "Escape"' in script
    assert 'event.key !== "Tab"' in script
    assert 'form.dataset.submitting === "true"' in script
    assert 'form.setAttribute("aria-busy", "true")' in script

    assert client.get("/admin/payments?detail=PMT-99999999").status_code == 404


def test_card_detail_is_informational_and_does_not_invent_provider_data(session, client):
    staff = _staff(session)
    _buyer_card, _order_card, attempt = _payment_graph(
        session,
        code="PMT-00000012",
        status=PaymentStatus.APPROVED,
        amount="89.99",
        method=PaymentMethod.CARD,
    )
    session.commit()
    _login(client, staff)

    body = client.get(f"/admin/payments?detail={attempt.public_code}").get_data(as_text=True)
    assert "Tarjeta" in body
    for literal in ("Stripe", "VISA", "4242", "Aprobar pago", "Rechazar pago"):
        assert literal not in body


def test_payment_proof_route_serves_exact_pmt_privately_and_rejects_tampering(
    session, client, app, tmp_path, monkeypatch
):
    staff = _staff(session)
    buyer_one, _order_one, attempt_one = _payment_graph(
        session, code="PMT-00000020", status=PaymentStatus.PROCESSING
    )
    _buyer_two, _order_two, attempt_two = _payment_graph(
        session, code="PMT-00000021", status=PaymentStatus.PROCESSING
    )
    proof_one = _proof(session, attempt_one)
    proof_two = _proof(session, attempt_two)

    payload_one = b"proof-one-exact-content"
    payload_two = b"proof-two-other-content"
    proof_one.storage_key = "2026/08/proof-one.pdf"
    proof_one.original_filename = "../comprobante-uno.pdf"
    proof_one.size_bytes = len(payload_one)
    proof_one.sha256 = hashlib.sha256(payload_one).hexdigest()
    proof_two.storage_key = "2026/08/proof-two.pdf"
    proof_two.original_filename = "../comprobante-dos.pdf"
    proof_two.size_bytes = len(payload_two)
    proof_two.sha256 = hashlib.sha256(payload_two).hexdigest()
    root = Path(tmp_path)
    (root / "2026" / "08").mkdir(parents=True)
    (root / proof_one.storage_key).write_bytes(payload_one)
    (root / proof_two.storage_key).write_bytes(payload_two)
    monkeypatch.setitem(app.config, "PAYMENT_PROOF_UPLOAD_DIR", str(root))
    session.commit()

    _login(client, buyer_one)
    assert client.get(f"/admin/payments/{attempt_one.public_code}/proof").status_code == 403

    _login(client, staff)
    inline = client.get(f"/admin/payments/{attempt_one.public_code}/proof")
    assert inline.status_code == 200
    assert inline.data == payload_one
    assert payload_two not in inline.data
    assert inline.headers["Cache-Control"] == "private, no-store"
    assert inline.headers["Pragma"] == "no-cache"
    assert inline.headers["X-Content-Type-Options"] == "nosniff"
    assert "inline" in inline.headers["Content-Disposition"]
    assert "comprobante-uno.pdf" in inline.headers["Content-Disposition"]

    download = client.get(f"/admin/payments/{attempt_one.public_code}/proof?download=1")
    assert download.status_code == 200
    assert "attachment" in download.headers["Content-Disposition"]

    (root / proof_one.storage_key).write_bytes(b"tampered")
    assert client.get(f"/admin/payments/{attempt_one.public_code}/proof").status_code == 404


def test_admin_payment_gets_do_not_mutate_financial_or_audit_state(session, client):
    staff = _staff(session)
    _buyer_payment, _order_payment, attempt = _payment_graph(
        session, code="PMT-00000030", status=PaymentStatus.AWAITING_PROOF
    )
    session.commit()
    before = {
        "attempts": session.scalar(select(func.count()).select_from(PaymentAttempt)),
        "proofs": session.scalar(select(func.count()).select_from(PaymentProof)),
        "audit": session.scalar(select(func.count()).select_from(AdminAuditEvent)),
    }
    _login(client, staff)

    assert client.get("/admin/payments").status_code == 200
    assert client.get(f"/admin/payments?detail={attempt.public_code}").status_code == 200
    db.session.remove()
    after = {
        "attempts": session.scalar(select(func.count()).select_from(PaymentAttempt)),
        "proofs": session.scalar(select(func.count()).select_from(PaymentProof)),
        "audit": session.scalar(select(func.count()).select_from(AdminAuditEvent)),
    }
    assert after == before


def test_p4_exposes_only_post_payment_decision_routes(app):
    payment_rules = [
        rule
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/admin/payments")
    ]
    assert payment_rules
    decision_rules = {
        rule.rule: rule.methods
        for rule in payment_rules
        if rule.rule.endswith("/approve") or rule.rule.endswith("/reject")
    }
    assert decision_rules == {
        "/admin/payments/<string:payment_code>/approve": {"OPTIONS", "POST"},
        "/admin/payments/<string:payment_code>/reject": {"OPTIONS", "POST"},
    }
