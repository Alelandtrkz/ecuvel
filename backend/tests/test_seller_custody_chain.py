from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest
from sqlalchemy import func, inspect, select, text
from flask_migrate import downgrade, upgrade

from app.models import (
    InventoryBalance, InventoryMovement, InventoryReservation, LogisticsPackageState, LogisticsTrackingEvent,
    Order, OrderItem, OrderPackage, PaymentAttempt, SellerOffer,
    SellerOrder, SellerPayout, SellerInboundPackage, PaymentProof, Warehouse, WarehouseLocation,
)
from app.models.enums import InventoryMovementType, LocationType, LogisticsPackageStatus, PackageStatus, PaymentMethod, ReservationStatus, SellerInboundPackageStatus, StaffRole
from app.services.checkout import CheckoutIdempotencyConflictError, CheckoutWarehouseError, create_checkout_order
from app.services.fulfillment import (
    InvalidPickupLocationError, OrderNotReadyForPackagingError, create_packages_for_order,
    prepare_order_for_pickup, handover_order_packages, order_ready_for_pickup_predicate,
    FulfillmentOrderNotFoundError, IncompletePackageScanError,
)
from app.services.inventory import pick_inventory_reservation
from app.services.partner_orders import approve_partner_order, PartnerOrderConflictError, PartnerOrderValidationError, PartnerOrderNotFoundError
from app.services.payment_proofs import review_payment_proof
from app.services.product_drafts import capture_submission_commission_snapshots
from app.services.product_publication import publish_product_draft
from app.services.seller_inbound_packages import (
    create_partner_inbound_package, mark_partner_inbound_package_ready, receive_seller_inbound_package,
)
from tests.factories import prepare_cart_confirmation, create_catalog_and_stock
from tests.product_moderation_helpers import (
    create_commission_rule, create_complete_simple_draft, create_phone_categories,
    create_store, create_user,
)
from app.services.admin_operating_context import operational_scope
from app.services.admin_orders import get_admin_order_detail
from app.services.admin_permissions import AdminAuthorizationError
from app.services.logistics_tracking import (
    assign_package_transfer, confirm_package_pickup, receive_transfer_at_destination,
    report_logistics_incident, LogisticsConflictError,
)
from tests.test_admin_rbac import staff_identity
from tests.test_admin_scanner import _point, _login, _set_point
from tests.test_payment_proofs import _submit
from tests.test_product_publication import _checklist, _enable_catalog_access


pytestmark = pytest.mark.integration


def published_sale(session, root, *, shops=1, approve_payment=True):
    buyer = create_user(session)
    buyer.birth_date = date(1990, 1, 1)
    operator = create_user(session, staff=True)
    parent, child = create_phone_categories(session)
    create_commission_rule(session, rate="8", category=parent)
    selections = {}
    owners = {}
    for _ in range(shops):
        store = create_store(session)
        seller = create_user(session)
        owners[store.id] = seller
        _enable_catalog_access(session, seller=seller, store=store)
        draft = create_complete_simple_draft(
            session, seller=seller, store=store, category=parent, subcategory=child,
            media_root=root / "drafts",
        )
        draft.inventory_data = {**draft.inventory_data, "stock_quantity": 10}
        capture_submission_commission_snapshots(session, draft)
        publish_product_draft(
            session, draft_id=draft.id, actor_user_id=operator.id, checklist=_checklist(),
            source_media_root=root / "drafts", catalog_media_root=root / "catalog",
        )
        offer = session.scalars(select(SellerOffer).where(SellerOffer.store_id == store.id)).one()
        balance = session.scalars(select(InventoryBalance).where(InventoryBalance.offer_id == offer.id)).one()
        assert balance.location.warehouse.seller_store_id == store.id
        assert (balance.on_hand_quantity, balance.available_quantity) == (10, 10)
        selections[str(offer.id)] = {"quantity": 2, "selected": True}
    checkout = create_checkout_order(
        session=session, buyer_id=buyer.id, payment_method=PaymentMethod.BANK_TRANSFER,
        confirmation=prepare_cart_confirmation(session, buyer.id, {"version": 1, "items": selections}),
        reservation_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    for balance in session.scalars(select(InventoryBalance).where(InventoryBalance.offer_id.in_(checkout.purchased_offer_ids))):
        assert (balance.on_hand_quantity, balance.reserved_quantity, balance.available_quantity) == (10, 2, 8)
    attempt = session.get(PaymentAttempt, checkout.payment_attempt_id)
    proof = _submit(session, root / "proofs", attempt, buyer.id, key=f"proof-{attempt.id}")
    if not approve_payment:
        return checkout, buyer, operator, owners
    review_payment_proof(
        session=session, proof_id=proof.proof_id, decision="approve",
        reviewer_user_id=operator.id, storage_root=root / "proofs",
    )
    for suborder in session.scalars(select(SellerOrder).where(SellerOrder.order_id == checkout.order_id)):
        approve_partner_order(session, user_id=owners[suborder.store_id].id, seller_order_id=suborder.id)
    return checkout, buyer, operator, owners


def ready_inbound(session, seller, suborder, allocations=None):
    package = create_partner_inbound_package(
        session, user_id=seller.id, seller_order_id=suborder.id, allocations=allocations,
        request_id=uuid.uuid4() if allocations is not None else None,
    )
    return mark_partner_inbound_package_ready(
        session, user_id=seller.id, seller_order_id=suborder.id, package_id=package.package_id,
    )


def point_with_staging(session, name):
    point, receiving = _point(session, name)
    staging = WarehouseLocation(
        warehouse_id=point.id, name="Retiro", code="RETIRO", barcode=f"RET-{point.code}",
        location_type=LocationType.PICKUP_STAGING, is_active=True,
    )
    session.add(staging)
    session.flush()
    return point, receiving, staging


def receive(session, package, location, actor, codes=None):
    return receive_seller_inbound_package(
        session, package_code=package.package_code, received_location_id=location.id,
        actor_user_id=actor.id, expected_warehouse_id=location.warehouse_id,
        verified_product_codes=codes if codes is not None else [i.sku for i in package.items for _ in range(i.quantity)],
    )


def test_published_sale_from_seller_stock_to_customer_in_one_point(app, session, tmp_path, monkeypatch):
    point, receiving, staging = point_with_staging(session, "W1")
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", point.code)
    order, buyer, operator, owners = published_sale(session, tmp_path)
    suborder = session.scalars(select(SellerOrder)).one()
    item = session.scalars(select(OrderItem)).one()
    reservation = session.scalars(select(InventoryReservation)).one()
    balance = session.get(InventoryBalance, reservation.balance_id)
    assert reservation.status == ReservationStatus.CONSUMED
    assert balance.location.warehouse_id != point.id
    original_balance = reservation.balance_id
    package = ready_inbound(session, owners[suborder.store_id], suborder)
    assert package.items[0].order_item_id == item.id
    assert session.scalar(select(LogisticsPackageState)) is None
    with pytest.raises(OrderNotReadyForPackagingError):
        create_packages_for_order(session=session, order_number=order.order_number)
    session.commit()
    client = app.test_client()
    _login(client, operator)
    _set_point(client, point.id)
    receipt = client.post("/admin/scanner/receive", data={
        "package_code": package.package_code, "received_location_id": str(receiving.id),
        "idempotency_key": "receipt", "verified_product_codes": [item.seller_sku_snapshot] * 2,
    })
    assert "result=received" in receipt.location
    session.expire_all()
    assert (balance.on_hand_quantity, balance.reserved_quantity, balance.available_quantity) == (8, 0, 8)
    state = session.scalars(select(LogisticsPackageState)).one()
    assert state.seller_inbound_package_id == package.package_id
    assert state.current_warehouse_id == state.custodian_warehouse_id == point.id
    assert session.scalar(select(OrderPackage)) is None
    path = f"/admin/orders/{order.order_number}"
    assert b"prepare-pickup" in client.get(path).data
    response = client.post(path + "/prepare-pickup", data={"pickup_location_id": str(staging.id)})
    assert response.status_code == 302
    outbound = session.scalars(select(OrderPackage)).one()
    assert outbound.order_item_id == item.id and outbound.quantity == item.quantity
    assert outbound.pickup_location_id == staging.id
    assert b"image/svg+xml;base64" in client.get(path + f"/packages/{outbound.package_code}/label").data
    movements = session.scalars(select(InventoryMovement.id).order_by(InventoryMovement.id)).all()
    before = (item.line_total, suborder.commission_total, order.total)
    proof_id = session.scalars(select(PaymentProof.id)).one()
    handover_data = {
        "buyer_code": buyer.public_account_code, "order_number": order.order_number,
        "identity_confirmed": "1", "scanned_codes": outbound.package_code, "idempotency_key": "deliver",
    }
    for _ in range(2):
        handover = client.post("/admin/scanner/handover", data=handover_data)
        assert "result=handed-over" in handover.location
    session.expire_all()
    assert outbound.status == PackageStatus.HANDED_OVER
    assert suborder.delivered_at == outbound.handed_over_at
    assert state.status == LogisticsPackageStatus.DELIVERED and state.custodian_user_id == buyer.id
    assert reservation.balance_id == original_balance
    assert (balance.on_hand_quantity, balance.reserved_quantity) == (8, 0)
    assert len(session.scalars(select(InventoryBalance)).all()) == 1
    assert movements == session.scalars(select(InventoryMovement.id).order_by(InventoryMovement.id)).all()
    assert before == (item.line_total, suborder.commission_total, session.get(Order, order.order_id).grand_total)
    assert session.scalars(select(PaymentAttempt)).one().id == order.payment_attempt_id
    assert session.scalars(select(PaymentProof.id)).one() == proof_id
    detail = get_admin_order_detail(session, scope=operational_scope(session, operator), order_number=order.order_number)
    assert detail.payment.attempt_id == order.payment_attempt_id
    assert session.scalar(select(SellerPayout.id)) is None


def prepare(session, order, staging, actor):
    return prepare_order_for_pickup(
        session, order_number=order.order_number, warehouse_id=staging.warehouse_id,
        pickup_location_id=staging.id, actor_user_id=actor.id,
    )


def test_split_multistore_transfer_and_intermediate_point(app, session, tmp_path, monkeypatch):
    w1, reception, staging1 = point_with_staging(session, "W1")
    w2, destination, staging2 = point_with_staging(session, "W2")
    w3, _, staging3 = point_with_staging(session, "W3")
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", w2.code)
    order, buyer, supervisor, owners = published_sale(session, tmp_path, shops=2)
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", w3.code)
    assert session.get(Order, order.order_id).pickup_warehouse_id == w2.id
    sub_a, sub_b = session.scalars(select(SellerOrder).order_by(SellerOrder.id)).all()
    a, b = sub_a.items[0], sub_b.items[0]
    seller = owners[sub_a.store_id]
    with pytest.raises(PartnerOrderValidationError):
        ready_inbound(session, seller, sub_a, [(b.id, 1)])
    with pytest.raises(PartnerOrderNotFoundError):
        ready_inbound(session, seller, sub_b)
    first, second = [ready_inbound(session, seller, sub_a, [(a.id, 1)]) for _ in range(2)]
    with pytest.raises(PartnerOrderConflictError):
        ready_inbound(session, seller, sub_a)
    third = ready_inbound(session, owners[sub_b.store_id], sub_b)
    receive(session, first, reception, supervisor)
    balance_a = session.scalars(select(InventoryBalance).where(InventoryBalance.offer_id == a.offer_id)).one()
    assert (balance_a.on_hand_quantity, balance_a.reserved_quantity) == (10, 2)
    receive(session, second, destination, supervisor)
    assert (balance_a.on_hand_quantity, balance_a.reserved_quantity) == (8, 0)
    with pytest.raises(OrderNotReadyForPackagingError):
        prepare(session, order, staging2, supervisor)
    assert not session.scalar(select(Order.id).where(order_ready_for_pickup_predicate()))
    carrier = staff_identity(session, StaffRole.TRANSPORT_OPERATOR)
    transfer = assign_package_transfer(
        session, package_code=first.package_code, destination_warehouse_id=w2.id,
        responsible_user_id=carrier.id, actor_user_id=supervisor.id,
    ).transfer
    confirm_package_pickup(session, package_code=first.package_code, actor_user_id=carrier.id,
                           expected_origin_warehouse_id=w1.id)
    receive(session, third, destination, supervisor)
    incident = report_logistics_incident(
        session, package_code=first.package_code, warehouse_id=w3.id,
        actor_user_id=staff_identity(session, StaffRole.POINT_OPERATOR, point=w3).id,
        reason="Llegada equivocada", idempotency_key="wrong-point",
    )
    assert incident.package_state.custodian_user_id == carrier.id
    assert incident.package_state.status == LogisticsPackageStatus.IN_TRANSIT
    assert incident.transfer.id == transfer.id and transfer.destination_warehouse_id == w2.id
    with pytest.raises(OrderNotReadyForPackagingError):
        prepare(session, order, staging2, supervisor)
    for staging in (staging1, staging3):
        with pytest.raises(InvalidPickupLocationError):
            prepare(session, order, staging, supervisor)
    assert session.scalar(select(OrderPackage.id)) is None
    receive_transfer_at_destination(
        session, package_code=first.package_code, warehouse_id=w2.id,
        location_id=destination.id, actor_user_id=supervisor.id,
    )
    operator = staff_identity(session, StaffRole.POINT_OPERATOR, point=w2)
    staged = prepare(session, order, staging2, operator)
    assert {p.order_item_id for p in staged} == {a.id, b.id}
    assert session.scalars(select(Order.id).where(order_ready_for_pickup_predicate())).one() == order.order_id
    with pytest.raises(LogisticsConflictError):
        assign_package_transfer(session, package_code=first.package_code, destination_warehouse_id=w3.id,
                                responsible_user_id=carrier.id, actor_user_id=supervisor.id)
    params = dict(session=session, order_number=order.order_number, expected_warehouse_id=w2.id,
                  actor_user_id=operator.id, expected_buyer_id=buyer.id, scanned_codes=[p.package_code for p in staged])
    with pytest.raises(IncompletePackageScanError):
        handover_order_packages(**{**params, "scanned_codes": params["scanned_codes"][:1]})
    with pytest.raises(FulfillmentOrderNotFoundError):
        handover_order_packages(**{**params, "expected_buyer_id": seller.id})
    with pytest.raises(InvalidPickupLocationError):
        handover_order_packages(**{**params, "expected_warehouse_id": w1.id, "actor_user_id": supervisor.id})
    assert not handover_order_packages(**params).replayed
    assert handover_order_packages(**params).replayed
    assert all(s.custodian_user_id == buyer.id for s in session.scalars(select(LogisticsPackageState)))
    assert sub_a.delivered_at == sub_b.delivered_at
    assert session.scalar(select(SellerPayout.id)) is None


def test_reception_rejections_and_preexisting_pick(app, session, tmp_path, monkeypatch):
    point, loc, staging = point_with_staging(session, "W1")
    other, loc2 = _point(session, "W2")
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", point.code)
    order, _, operator, owners = published_sale(session, tmp_path)
    suborder = session.scalars(select(SellerOrder)).one()
    item, = suborder.items
    owner = owners[suborder.store_id]
    package = create_partner_inbound_package(session, user_id=owner.id, seller_order_id=suborder.id)
    # Terminal historical input. There is no application command to cancel an inbound parcel.
    cancelled = SellerInboundPackage(
        seller_order_id=suborder.id, package_code="PKG-999999999999", barcode="PKG-999999999999",
        status=SellerInboundPackageStatus.CANCELLED, cancelled_at=datetime.now(timezone.utc),
    )
    from app.models import SellerInboundPackageItem
    session.add(cancelled)
    session.flush()
    session.add(SellerInboundPackageItem(package_id=cancelled.id, order_item_id=item.id, quantity=2))
    session.flush()
    with pytest.raises(PartnerOrderConflictError):
        receive_seller_inbound_package(
            session, package_code=cancelled.package_code, received_location_id=loc.id,
            actor_user_id=operator.id, verified_product_codes=[item.seller_sku_snapshot] * 2,
        )
    with pytest.raises(PartnerOrderConflictError):
        receive(session, package, loc, operator)
    mark_partner_inbound_package_ready(session, user_id=owner.id, seller_order_id=suborder.id, package_id=package.package_id)
    for codes in ([item.seller_sku_snapshot], [item.seller_sku_snapshot] * 3, ["otro"] * 2):
        with pytest.raises(PartnerOrderValidationError):
            receive(session, package, loc, operator, codes)
    with pytest.raises(AdminAuthorizationError):
        receive(session, package, loc, staff_identity(session, StaffRole.POINT_OPERATOR, point=other))
    assert session.scalar(select(LogisticsTrackingEvent.id)) is None
    balance = session.scalars(select(InventoryBalance)).one()
    assert (balance.on_hand_quantity, balance.reserved_quantity) == (10, 2)
    reservation = session.scalars(select(InventoryReservation)).one()
    picked = pick_inventory_reservation(session=session, reservation_id=reservation.id, actor_user_id=operator.id)
    with pytest.raises(OrderNotReadyForPackagingError, match="recepción física"):
        prepare(session, order, staging, operator)
    receive(session, package, loc, operator)
    with pytest.raises(PartnerOrderConflictError):
        receive(session, package, loc2, operator)
    assert receive(session, package, loc, operator).replayed
    assert session.scalars(select(InventoryMovement.id).where(
        InventoryMovement.movement_type == InventoryMovementType.PICK)).one() == picked.movement_id
    assert (balance.on_hand_quantity, balance.reserved_quantity) == (8, 0)


def test_parallel_reception_preparation_and_delivery(app, session, session_factory, concurrent_runner, tmp_path, monkeypatch):
    point, loc, staging = point_with_staging(session, "W1")
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", point.code)
    order, buyer, _, owners = published_sale(session, tmp_path)
    suborder = session.scalars(select(SellerOrder)).one()
    package = ready_inbound(session, owners[suborder.store_id], suborder)
    operators = [staff_identity(session, StaffRole.POINT_OPERATOR, point=point).id for _ in range(2)]
    session.commit()

    def run_pair(operation):
        def worker(actor_id):
            def perform(barrier):
                with session_factory.begin() as tx:
                    pid = tx.scalar(text("select pg_backend_pid()"))
                    barrier.wait()
                    return pid, operation(tx, actor_id)
            return perform
        results, errors = concurrent_runner([worker(actor) for actor in operators])
        assert not errors
        assert len({pid for pid, _ in results}) == 2
        assert sorted(replayed for _, replayed in results) == [False, True]

    def receipt(tx, actor):
        return receive_seller_inbound_package(
            tx, package_code=package.package_code, received_location_id=loc.id, actor_user_id=actor,
            verified_product_codes=[package.items[0].sku] * 2,
        ).replayed
    run_pair(receipt)
    run_pair(lambda tx, actor: prepare_order_for_pickup(
        tx, order_number=order.order_number, pickup_location_id=staging.id, warehouse_id=point.id,
        actor_user_id=actor,
    )[0].replayed)
    outbound = session.scalars(select(OrderPackage)).one()
    run_pair(lambda tx, actor: handover_order_packages(
        session=tx, order_number=order.order_number, expected_buyer_id=buyer.id, expected_warehouse_id=point.id,
        actor_user_id=actor, scanned_codes=[outbound.package_code],
    ).replayed)
    session.expire_all()
    assert outbound.status == PackageStatus.HANDED_OVER
    balance = session.scalars(select(InventoryBalance)).one()
    assert (balance.on_hand_quantity, balance.reserved_quantity) == (8, 0)
    assert len(session.scalars(select(LogisticsTrackingEvent.id)).all()) == 1
    assert session.scalar(select(func.count(InventoryMovement.id)).where(
        InventoryMovement.movement_type == InventoryMovementType.PICK)) == 1


@pytest.mark.parametrize("failure", ["custody", "pick", "pack", "stage"])
def test_operation_rollback_and_retry(app, session, session_factory, tmp_path, monkeypatch, failure):
    from app.services import fulfillment, seller_inbound_packages
    point, loc, staging = point_with_staging(session, "W1")
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", point.code)
    order, _, operator, owners = published_sale(session, tmp_path)
    suborder = session.scalars(select(SellerOrder)).one()
    package = ready_inbound(session, owners[suborder.store_id], suborder)
    reception_failure = failure in ("custody", "pick")
    if not reception_failure:
        receive(session, package, loc, operator)
    session.commit()
    module, method = {
        "custody": (seller_inbound_packages, "register_received_inbound_package"),
        "pick": (seller_inbound_packages, "pick_inventory_reservation"),
        "pack": (fulfillment, "pack_order_package"),
        "stage": (fulfillment, "stage_order_package_for_pickup"),
    }[failure]
    original = getattr(module, method)

    def fail_after_flush(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("fallo inyectado")

    def operation(tx):
        if reception_failure:
            return receive(tx, package, loc, operator)
        return prepare(tx, order, staging, operator)

    with monkeypatch.context() as patch:
        patch.setattr(module, method, fail_after_flush)
        with session_factory.begin() as tx:
            with pytest.raises(RuntimeError, match="fallo inyectado"):
                operation(tx)
    session.expire_all()
    balance = session.scalars(select(InventoryBalance)).one()
    assert (balance.on_hand_quantity, balance.reserved_quantity) == ((10, 2) if reception_failure else (8, 0))
    assert session.scalar(select(func.count(LogisticsTrackingEvent.id))) == (0 if reception_failure else 1)
    assert session.scalar(select(OrderPackage.id)) is None
    with session_factory.begin() as tx:
        operation(tx)


def test_checkout_pins_valid_destination_and_replays_original(app, session, monkeypatch):
    base = create_catalog_and_stock(session)
    first = session.get(Warehouse, base.warehouse_id)
    other, _ = _point(session, "W2")
    seller = create_store(session)
    from app.services.marketplace_policy import ensure_store_inventory_location
    commercial = ensure_store_inventory_location(session, store=seller).warehouse
    cart = {"version": 1, "items": {str(base.offer_id): {"quantity": 1, "selected": True}}}
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", first.code)
    confirmation = prepare_cart_confirmation(session, base.buyer_id, cart)
    params = dict(session=session, buyer_id=base.buyer_id, payment_method=PaymentMethod.BANK_TRANSFER,
                  confirmation=confirmation, reservation_expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    for code in ("", "missing", commercial.code):
        monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", code)
        with pytest.raises(CheckoutWarehouseError):
            create_checkout_order(**params)
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", other.code)
    with pytest.raises(CheckoutIdempotencyConflictError):
        create_checkout_order(**params)
    other.is_active = False
    session.flush()
    with pytest.raises(CheckoutWarehouseError):
        create_checkout_order(**params)
    assert session.scalar(select(Order.id)) is None
    assert session.scalar(select(InventoryReservation.id)) is None
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", first.code)
    result = create_checkout_order(**params)
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", other.code)
    assert create_checkout_order(**params).replayed
    assert session.get(Order, result.order_id).pickup_warehouse_id == first.id


def test_destination_upgrade_downgrade_leaves_history_unknown(app, session, engine, tmp_path, monkeypatch):
    point, receiving, staging = point_with_staging(session, "W1")
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", point.code)
    order, _, actor, owners = published_sale(session, tmp_path)
    suborder = session.scalars(select(SellerOrder)).one()
    package = ready_inbound(session, owners[suborder.store_id], suborder)
    receive(session, package, receiving, actor)
    order_id = order.order_id
    session.commit()
    directory = str(Path(app.root_path).parent / "migrations")
    try:
        downgrade(directory=directory, revision="a9b0c1d2e3f4")
        assert "pickup_warehouse_id" not in {c["name"] for c in inspect(engine).get_columns("orders")}
        with engine.connect() as conn:
            assert conn.scalar(text("select count(*) from orders where id=:id"), {"id": order_id}) == 1
    finally:
        upgrade(directory=directory)
    fks = inspect(engine).get_foreign_keys("orders")
    fk = next(fk for fk in fks if fk["constrained_columns"] == ["pickup_warehouse_id"])
    assert fk["referred_table"] == "warehouses" and fk["options"]["ondelete"] == "RESTRICT"
    session.expire_all()
    assert session.get(Order, order_id).pickup_warehouse_id is None
    assert not session.scalar(select(Order.id).where(Order.id == order_id, order_ready_for_pickup_predicate()))
    with pytest.raises(InvalidPickupLocationError):
        prepare(session, order, staging, actor)
    assert session.scalars(select(LogisticsPackageState.seller_inbound_package_id)).one() == package.package_id
    assert session.scalar(select(OrderPackage.id)) is None


def test_partial_allocation_request_replay(app, session, tmp_path, monkeypatch):
    point, _, _ = point_with_staging(session, "W1")
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", point.code)
    _, _, _, owners = published_sale(session, tmp_path)
    suborder = session.scalars(select(SellerOrder)).one()
    item, = suborder.items
    params = dict(user_id=owners[suborder.store_id].id, seller_order_id=suborder.id,
                  allocations=[(item.id, 1)], request_id=uuid.uuid4())
    first = create_partner_inbound_package(session, **params)
    replay = create_partner_inbound_package(session, **params)
    assert replay.replayed and first.package_id == replay.package_id
    with pytest.raises(PartnerOrderConflictError):
        create_partner_inbound_package(session, **{**params, "allocations": [(item.id, 2)]})
    second = create_partner_inbound_package(session, **{**params, "request_id": uuid.uuid4()})
    assert second.package_id != first.package_id


def test_payment_consumption_rollback_and_unpaid_preparation(app, session, session_factory, tmp_path, monkeypatch):
    from app.models.enums import PaymentStatus, PaymentProofStatus
    from app.services import payment_proofs
    point, _, _ = point_with_staging(session, "W1")
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", point.code)
    order, _, actor, owners = published_sale(session, tmp_path, approve_payment=False)
    suborder = session.scalars(select(SellerOrder)).one()
    with pytest.raises(PartnerOrderConflictError):
        ready_inbound(session, owners[suborder.store_id], suborder)
    proof_id = session.scalars(select(PaymentProof.id)).one()
    session.commit()
    consume = payment_proofs.consume_inventory_reservation

    def fail_after_consumption(**kwargs):
        consume(**kwargs)
        raise RuntimeError("fallo posterior al consumo")

    with monkeypatch.context() as patch:
        patch.setattr(payment_proofs, "consume_inventory_reservation", fail_after_consumption)
        with pytest.raises(RuntimeError, match="posterior al consumo"), session_factory.begin() as tx:
            review_payment_proof(session=tx, proof_id=proof_id, decision="approve",
                                 reviewer_user_id=actor.id, storage_root=tmp_path / "proofs")
    session.expire_all()
    assert session.scalars(select(InventoryReservation.status)).one() == ReservationStatus.ACTIVE
    assert session.get(PaymentProof, proof_id).status == PaymentProofStatus.PENDING_REVIEW
    assert session.get(PaymentAttempt, order.payment_attempt_id).status == PaymentStatus.PROCESSING
    assert session.scalar(select(SellerInboundPackage.id)) is None
    balance = session.scalars(select(InventoryBalance)).one()
    assert (balance.on_hand_quantity, balance.reserved_quantity) == (10, 2)


@pytest.mark.parametrize("arrival_type", [LocationType.RECEIVING, LocationType.QUARANTINE])
def test_intermediate_transfer_destination_does_not_authorize_pickup(app, session, tmp_path, monkeypatch, arrival_type):
    w1, reception, _ = point_with_staging(session, "Origen")
    intermediate, stop, staging_stop = point_with_staging(session, "Escala")
    final, arrival, staging_final = point_with_staging(session, "Destino comprador")
    if arrival_type == LocationType.QUARANTINE:
        arrival = WarehouseLocation(
            warehouse_id=final.id, code="CUARENTENA", barcode="LOC-CUARENTENA",
            name="Cuarentena", location_type=arrival_type, is_active=True,
        )
        session.add(arrival)
        session.flush()
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", final.code)
    order, _, supervisor, owners = published_sale(session, tmp_path)
    suborder = session.scalars(select(SellerOrder)).one()
    package = ready_inbound(session, owners[suborder.store_id], suborder)
    receive(session, package, reception, supervisor)
    carrier = staff_identity(session, StaffRole.TRANSPORT_OPERATOR)

    def transfer(origin, destination, location):
        assign_package_transfer(session, package_code=package.package_code, destination_warehouse_id=destination.id,
                                responsible_user_id=carrier.id, actor_user_id=supervisor.id)
        confirm_package_pickup(session, package_code=package.package_code, actor_user_id=carrier.id,
                               expected_origin_warehouse_id=origin.id)
        return receive_transfer_at_destination(session, package_code=package.package_code,
                                               warehouse_id=destination.id, location_id=location.id,
                                               actor_user_id=supervisor.id).package_state

    custody = transfer(w1, intermediate, stop)
    assert custody.expected_destination_warehouse_id == custody.custodian_warehouse_id == intermediate.id
    assert session.get(Order, order.order_id).pickup_warehouse_id == final.id
    with pytest.raises(InvalidPickupLocationError):
        prepare(session, order, staging_stop, supervisor)
    assert session.scalar(select(OrderPackage.id)) is None
    transfer(intermediate, final, arrival)
    if arrival_type == LocationType.QUARANTINE:
        with pytest.raises(OrderNotReadyForPackagingError):
            prepare(session, order, staging_final, supervisor)
        assert session.scalar(select(OrderPackage.id)) is None
    else:
        assert prepare(session, order, staging_final, supervisor)[0].pickup_location_id == staging_final.id


def test_demo_order_writer_also_pins_configured_destination(app, session, monkeypatch):
    # Cover the other Order constructor. The marketplace journey above publishes and checks out independently.
    runner = app.test_cli_runner()
    for command in ("seed-demo", "receive-demo-stock", "putaway-demo-stock"):
        result = runner.invoke(args=[command])
        assert result.exit_code == 0, result.exception
    final, _ = _point(session, "Retiro configurado")
    session.commit()
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", "")
    assert runner.invoke(args=["reserve-demo-stock"]).exit_code != 0
    assert session.scalar(select(Order.id)) is None
    monkeypatch.setitem(app.config, "ECUVEL_PICKUP_WAREHOUSE_CODE", final.code)
    result = runner.invoke(args=["reserve-demo-stock"])
    assert result.exit_code == 0, result.exception
    assert session.scalars(select(Order.pickup_warehouse_id)).one() == final.id
