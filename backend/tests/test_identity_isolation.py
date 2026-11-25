"""Contratos de aislamiento entre identidades, con datos sintéticos en PostgreSQL."""
from datetime import date, datetime, timezone
from hashlib import sha256
from io import BytesIO

import pytest
from sqlalchemy import select

from app.models import (
    CartItem, InventoryBalance, InventoryMovement, InventoryReservation, Order, OrderItem,
    PaymentAttempt, PaymentNotificationOutbox, PaymentProof, ProductDraft, ProductDraftFile,
    ProductDraftModerationEvent, ProductVariant, SellerInboundPackage, SellerOffer, SellerOrder,
    SellerPayout, StaffProfile, Store, StoreContractAcceptance, StoreMember, StoreOnboardingDocument, User,
)
from app.models.enums import (
    ProductDraftFileKind, ProductDraftFileStatus, SellerOrderDecisionStatus, StaffRole,
    StoreMemberRole, UserStatus,
)
from app.services import partner_onboarding, payment_proofs, pending_payments
from app.services.partner_orders import PartnerOrderAccessError, require_partner_order_store
from app.services.partner_product_categories import PartnerProductAccessError, require_partner_catalog_store
from app.services.partner_sales import PartnerSalesAccessError, require_partner_sales_store
from app.services.private_storage import stage_payment_proof
from tests.factories import create_catalog_and_stock
from tests.test_auth_session_revocation import _login, _user
from tests.test_auth_error_ux import _csrf_token
from tests.test_partner_my_products import _category, _draft, _login as login_partner, _partner
from tests.test_partner_orders import _enable_owner
from tests.test_payment_proofs import (
    PNG, PROOF_FORMAT_POLICY, _assign_staff_profile, _graph, _submit as attach_proof, _upload,
)
from tests.test_storefront_checkout import _checkout_token, _login_as, _prepare_demo_buyer, _set_cart, _submit


pytestmark = pytest.mark.integration


@pytest.mark.parametrize("active,status", [
    (True, UserStatus.SUSPENDED), (True, UserStatus.BLOCKED), (False, UserStatus.ACTIVE),
])
@pytest.mark.parametrize("policy,denial", [
    (require_partner_catalog_store, PartnerProductAccessError),
    (require_partner_order_store, PartnerOrderAccessError),
    (require_partner_sales_store, PartnerSalesAccessError),
])
def test_partner_policy_revalidates_account(session, policy, denial, active, status):
    owner, store = _partner(session)
    assert policy(session, owner.id).store_id == store.id
    owner.is_active = active
    owner.status = status
    session.commit()
    with pytest.raises(denial):
        policy(session, owner.id)


def test_authenticated_purchase_has_no_transferable_order_list(app, session):
    base = create_catalog_and_stock(session, stock=3)
    browser = app.test_client()
    _prepare_demo_buyer(session, base, app, browser)
    session.commit()
    _set_cart(browser, (base.offer_id, 1, True))
    assert "/checkout/transferencia/" in _submit(browser, _checkout_token(browser)).location
    assert session.scalar(select(Order.buyer_id)) == base.buyer_id
    with browser.session_transaction() as state:
        assert not bool(state.get("checkout_order_ids"))


def test_onboarding_owner_policy_revalidates_account(session):
    owner, _ = _partner(session)
    assert partner_onboarding.get_onboarding(session, owner.id).user_id == owner.id
    owner.is_active = False
    session.commit()
    has_application = partner_onboarding.get_onboarding(session, owner.id) is not None
    assert not has_application
    with pytest.raises(partner_onboarding.PartnerOnboardingError):
        partner_onboarding.get_or_create_onboarding(session, owner.id)


def test_switch_without_remember_discards_previous_remember_cookie(app, session):
    first = _user(session, email="first@identity.test")
    second = _user(session, email="second@identity.test")
    session.commit()
    browser = app.test_client()
    name = app.config.get("REMEMBER_COOKIE_NAME", "remember_token")
    assert _login(browser, first, remember=True).status_code == 302
    assert bool(browser.get_cookie(name))
    assert _login(browser, second, remember=False).status_code == 302
    has_remember_cookie = bool(browser.get_cookie(name))
    assert not has_remember_cookie
    browser.delete_cookie(app.config["SESSION_COOKIE_NAME"])
    assert browser.get("/pedidos").status_code == 302


def test_catalog_batch_receipt_belongs_to_current_membership(app, session):
    seller, first = _partner(session)
    _, second = _partner(session)
    parent, child = _category(session)
    draft = _draft(session, user=seller, store=first, parent=parent, child=child,
                   title="Borrador sintético reservado", sku="MATRIX-1")
    session.commit()
    browser = app.test_client()
    login_partner(browser, seller)
    response = browser.post("/partners/my-products/bulk/submit", data={"draft_ids": [str(draft.id)]})
    assert response.status_code == 302
    with browser.session_transaction() as state:
        receipt = state["partner_catalog_batch_result"]
        assert receipt["failures"]
        assert receipt.get("store_id") == str(first.id)
        assert receipt.get("user_id") == str(seller.id)
    membership = session.scalar(select(StoreMember).where(StoreMember.user_id == seller.id))
    membership.is_active = False
    session.add(StoreMember(user_id=seller.id, store_id=second.id,
                            role=StoreMemberRole.ADMINISTRATOR, is_active=True))
    session.commit()
    page = browser.get("/partners/my-products")
    assert page.status_code == 200
    assert draft.title not in page.get_data(as_text=True)


def test_buyer_commands_reject_before_processing_payment_graph(session, monkeypatch):
    base, _, _, _, attempt, outsider = _graph(session)
    session.get(User, base.buyer_id).is_active = False
    session.commit()

    def unexpected_processing(*args, **kwargs):
        pytest.fail("La operación entró en el procesamiento financiero sin autorizar al comprador.")

    monkeypatch.setattr(pending_payments, "_release_active_reservations", unexpected_processing)
    before = persisted_state(session)
    for actor in (outsider.id, None, base.buyer_id):
        with pytest.raises(pending_payments.PendingPaymentNotFoundError):
            pending_payments.cancel_pending_bank_transfer_order(
                session=session, payment_attempt_id=attempt.id, actor_user_id=actor,
            )
    assert persisted_state(session) == before


def test_proof_authorizes_before_reading_graph(session, tmp_path, monkeypatch):
    _, _, _, _, attempt, outsider = _graph(session)
    session.commit()

    def unexpected_read(*args, **kwargs):
        pytest.fail("Se consultó el detalle financiero antes de autorizar al comprador.")

    monkeypatch.setattr(payment_proofs, "_order_graph", unexpected_read)
    with pytest.raises(payment_proofs.PaymentProofServiceError):
        attach_proof(session, tmp_path, attempt, outsider.id)


def test_proof_replay_checks_owner_and_preserves_files(session, tmp_path):
    base, _, _, _, attempt, outsider = _graph(session)
    first = attach_proof(session, tmp_path, attempt, base.buyer_id)
    session.get(User, base.buyer_id).is_active = False
    session.commit()
    staged = stage_payment_proof(_upload(), root=tmp_path, max_bytes=10000, **PROOF_FORMAT_POLICY)
    paths = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    before = persisted_state(session)
    for actor in (None, outsider.id, base.buyer_id):
        with pytest.raises(payment_proofs.PaymentProofServiceError):
            payment_proofs.submit_bank_transfer_proof(
                session=session, payment_attempt_id=attempt.id, staged_file=staged,
                upload_idempotency_key="upload-key", storage_root=tmp_path, uploaded_by_user_id=actor,
            )
    assert persisted_state(session) == before
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == paths
    assert first.storage_path.read_bytes() == PNG


def persisted_state(session):
    models = (
        Order, SellerOrder, OrderItem, PaymentAttempt, PaymentProof, InventoryReservation,
        InventoryBalance, InventoryMovement, CartItem, ProductDraft, ProductDraftFile, SellerOffer, ProductVariant,
        SellerPayout, Store, StaffProfile, StoreContractAcceptance, SellerInboundPackage,
        PaymentNotificationOutbox, ProductDraftModerationEvent, StoreOnboardingDocument,
    )
    return [session.execute(select(model.__table__).order_by(model.id)).all() for model in models]


def test_two_buyers_and_two_stores_share_one_marketplace(app, session, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "PAYMENT_PROOF_UPLOAD_DIR", str(tmp_path))
    left, right = (create_catalog_and_stock(session) for _ in range(2))
    sellers = [_enable_owner(session, base) for base in (left, right)]
    buyer = session.get(User, left.buyer_id)
    buyer.email_verified_at = datetime.now(timezone.utc)
    sellers[1].birth_date = date(1990, 1, 1)
    admin = _user(session, staff=True)
    _assign_staff_profile(session, admin, role=StaffRole.SUPER_ADMIN)
    offers = [session.get(SellerOffer, base.offer_id) for base in (left, right)]
    for offer in offers:
        offer.variant.product.title = f"Producto {offer.seller_sku}"
    session.commit()

    customer, seller_a, seller_b = (app.test_client() for _ in range(3))
    for browser, identity in zip((customer, seller_a, seller_b), (buyer, *sellers)):
        _login_as(browser, identity.id)
    _set_cart(customer, (left.offer_id, 1, True), (right.offer_id, 2, True))
    token = _checkout_token(customer)
    assert "/transferencia/" in _submit(customer, token).location
    _set_cart(seller_b, (left.offer_id, 1, True))
    assert "/transferencia/" in _submit(seller_b, _checkout_token(seller_b)).location
    mixed = session.scalar(select(Order).where(Order.buyer_id == buyer.id))
    separate = session.scalar(select(Order).where(Order.buyer_id == sellers[1].id))
    assert mixed.id != separate.id

    attempt = session.scalar(select(PaymentAttempt).where(PaymentAttempt.order_id == mixed.id))
    proof = attach_proof(session, tmp_path, attempt, buyer.id)
    payment_proofs.review_payment_proof(session=session, proof_id=proof.proof_id, decision="approve",
                                        reviewer_user_id=admin.id, storage_root=tmp_path)
    session.commit()
    own_page = customer.get(f"/pedidos/{mixed.order_number}")
    assert own_page.status_code == 200
    assert all(offer.variant.product.title in own_page.get_data(as_text=True) for offer in offers)
    assert seller_b.get(f"/pedidos/{separate.order_number}").status_code == 200
    proof_url = f"/pagos/comprobantes/{proof.proof_id}/archivo"
    assert customer.get(proof_url).data == PNG

    before = persisted_state(session)
    assert customer.get(f"/pedidos/{separate.order_number}").status_code == 404
    assert customer.post(f"/pedidos/{separate.order_number}/cancelar").status_code == 404
    assert seller_b.get(f"/pedidos/{mixed.order_number}").status_code == 404
    foreign_proof = seller_b.get(proof_url)
    assert foreign_proof.status_code == 404 and PNG not in foreign_proof.data
    anon = app.test_client().get(proof_url)
    assert anon.status_code == 302 and PNG not in anon.data
    parts = {part.store_id: part for part in session.scalars(
        select(SellerOrder).where(SellerOrder.order_id == mixed.id)
    )}
    for browser, own, other in ((seller_a, left, right), (seller_b, right, left)):
        detail = browser.get(f"/partners/orders/{parts[own.store_id].id}/detail")
        assert detail.status_code == 200
        view = detail.json["order"]
        sku = session.get(SellerOffer, own.offer_id).seller_sku
        assert [line["sku"] for line in view["lines"]] == [sku]
        assert view["financials"]["product_subtotal"] == str(parts[own.store_id].subtotal)
        assert session.get(SellerOffer, other.offer_id).seller_sku not in detail.get_data(as_text=True)
        foreign = f"/partners/orders/{parts[other.store_id].id}"
        assert browser.get(f"{foreign}/detail").status_code == 404
        assert browser.post(f"{foreign}/approve").status_code == 404
        assert browser.post(f"{foreign}/packages", json={"store_id": str(other.store_id)}).status_code == 404
        assert browser.post(f"{foreign}/reject", data={"reason": "OTHER", "comment": "Prueba local"}).status_code == 404
    assert persisted_state(session) == before
    assert proof.storage_path.read_bytes() == PNG
    assert seller_a.post(f"/partners/orders/{parts[left.store_id].id}/approve").status_code == 200
    session.expire_all()
    assert parts[left.store_id].decision_status == SellerOrderDecisionStatus.APPROVED
    assert parts[right.store_id].decision_status == SellerOrderDecisionStatus.PENDING


def test_system_cancellation_preserves_audit_attribution(app, session):
    _, order_id, number, _, _, _ = _graph(session)
    session.commit()
    result = app.test_cli_runner().invoke(args=["cancel-pending-order", "--order-number", number])
    assert result.exit_code == 0
    session.expire_all()
    assert session.get(Order, order_id).status.value == "CANCELLED"
    movements = session.scalars(select(InventoryMovement)).all()
    released = [row for row in movements if row.movement_type.value == "RELEASE"]
    assert released and all(row.actor_user_id is None for row in released)


def test_draft_files_and_bulk_actions_stay_with_store(app, session, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "PARTNER_PRODUCT_DRAFT_UPLOAD_DIR", str(tmp_path))
    a, store_a = _partner(session)
    b, store_b = _partner(session)
    parent, child = _category(session)
    drafts = [
        _draft(session, user=user, store=store, parent=parent, child=child, sku=sku, title=sku)
        for user, store, sku in ((a, store_a, "BORRADOR-A"), (b, store_b, "BORRADOR-B"))
    ]
    marker = b"private synthetic draft B"
    storage_key = "drafts/sample-b.png"
    path = tmp_path / storage_key
    path.parent.mkdir()
    path.write_bytes(marker)
    image = ProductDraftFile(
        draft_id=drafts[1].id, kind=ProductDraftFileKind.IMAGE, status=ProductDraftFileStatus.ACTIVE,
        storage_key=storage_key, original_filename="sample-b.png", media_type="image/png",
        sha256=sha256(marker).hexdigest(), size_bytes=len(marker), position=0, is_cover=True,
    )
    session.add(image)
    session.commit()
    first, second = app.test_client(), app.test_client()
    login_partner(first, a)
    login_partner(second, b)
    url = f"/partners/products/drafts/{drafts[1].id}/files/{image.id}"
    assert second.get(url).data == marker
    before = persisted_state(session)
    denied = first.get(url)
    assert denied.status_code == 404 and marker not in denied.data
    saved = first.post(f"/partners/products/drafts/{drafts[1].id}/save", data={
        "store_id": str(store_b.id), "user_id": str(b.id), "title": "Cambio de prueba",
    })
    assert saved.status_code == 404
    public = first.get(f"/static/{storage_key}")
    assert public.status_code == 404 and marker not in public.data
    nested = first.post(f"/partners/products/drafts/{drafts[0].id}/files/{image.id}/delete")
    assert nested.status_code in (302, 404)
    for action in ("delete", "submit"):
        response = first.post(f"/partners/my-products/bulk/{action}",
                              data={"draft_ids": [str(d.id) for d in drafts]})
        assert response.status_code == 404
    assert persisted_state(session) == before
    assert [f for f in tmp_path.rglob("*") if f.is_file()] == [path]
    assert path.read_bytes() == marker


def test_logout_then_login_changes_checkout_identity(app, session):
    base = create_catalog_and_stock(session)
    a, b = _user(session), _user(session)
    a.birth_date = b.birth_date = date(1990, 1, 1)
    session.commit()
    browser = app.test_client()
    assert _login(browser, a).status_code == 302
    _set_cart(browser, (base.offer_id, 1, True))
    token = _checkout_token(browser)
    assert "/transferencia/" in _submit(browser, token).location
    assert browser.post("/cerrar-sesion").status_code == 302
    assert _login(browser, b).status_code == 302
    before = persisted_state(session)
    assert _submit(browser, token).location.endswith("/checkout")
    assert persisted_state(session) == before


def test_csrf_is_bound_to_browser_session(app, session, monkeypatch):
    owner, _ = _partner(session)
    session.commit()
    one, two = app.test_client(), app.test_client()
    for browser in (one, two):
        login_partner(browser, owner)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    with app.app_context():
        valid_for_one = _csrf_token(one.get("/partners/my-products"))
    with app.app_context():
        assert two.get("/partners/my-products").status_code == 200
    before = persisted_state(session)
    for form in ({}, {"csrf_token": "invalid"}, {"csrf_token": valid_for_one}):
        with app.app_context():
            assert two.post("/partners/my-products/bulk/delete", data=form).status_code == 400
    assert two.get("/partners/my-products/bulk/delete").status_code == 405
    assert persisted_state(session) == before


def test_revoked_draft_access_denied_before_file_io(app, session, tmp_path, monkeypatch):
    from app.blueprints import partners

    seller, store = _partner(session)
    parent, child = _category(session)
    draft = _draft(session, user=seller, store=store, parent=parent, child=child, sku="LOCAL", title="Local")
    session.commit()
    browser = app.test_client()
    login_partner(browser, seller)
    url = f"/partners/products/drafts/{draft.id}"
    assert browser.get(url).status_code == 200
    session.scalar(select(StoreMember).where(StoreMember.user_id == seller.id)).is_active = False
    session.commit()
    monkeypatch.setitem(app.config, "PARTNER_PRODUCT_DRAFT_UPLOAD_DIR", str(tmp_path))

    def unexpected_staging(*args, **kwargs):
        pytest.fail("Se inició la preparación de archivos antes de autorizar el borrador.")

    monkeypatch.setattr(partners, "stage_product_draft_upload", unexpected_staging)
    before = persisted_state(session)
    response = browser.post(f"{url}/files", data={"file": (BytesIO(PNG), "demo.png")})
    assert response.status_code == 404
    assert browser.get(url).status_code == 404
    assert persisted_state(session) == before
    assert not list(tmp_path.iterdir())
