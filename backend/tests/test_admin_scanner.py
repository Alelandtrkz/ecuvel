from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from werkzeug.datastructures import MultiDict

from app.models import (
    LogisticsPackageState,
    OrderItem,
    OrderPackage,
    SellerInboundPackage,
    SellerInboundPackageItem,
    SellerOrder,
    User,
    Warehouse,
    WarehouseLocation,
)
from app.models.enums import (
    LocationType,
    LogisticsPackageStatus,
    PackageStatus,
    SellerInboundPackageStatus,
)
from app.services.admin_scanner import get_admin_package_lookup
from app.services.logistics_tracking import (
    assign_package_transfer,
    confirm_package_pickup,
)
from tests.factories import (
    create_catalog_and_stock,
    create_order_items,
    create_ready_for_pickup_order,
)


pytestmark = pytest.mark.integration


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, user: User) -> None:
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)
        browser["_fresh"] = True


def _set_point(client, warehouse_id) -> None:
    with client.session_transaction() as browser:
        browser["admin_operating_warehouse_id"] = str(warehouse_id)


def _staff_from_base(session, base) -> User:
    staff = session.get(User, base.operator_id)
    staff.is_ecuvel_staff = True
    staff.is_active = True
    session.flush()
    return staff


def _point(session, name: str) -> tuple[Warehouse, WarehouseLocation]:
    token = uuid.uuid4().hex[:8]
    warehouse = Warehouse(
        code=f"P-{token}",
        name=name,
        address_line="Dirección de prueba",
        city="Quito",
        country_code="EC",
        is_active=True,
    )
    session.add(warehouse)
    session.flush()
    location = WarehouseLocation(
        warehouse_id=warehouse.id,
        code=f"REC-{token}",
        barcode=f"LOC-{token}",
        name=f"Recepción {name}",
        location_type=LocationType.RECEIVING,
        capacity_units=100,
        allows_mixed_offers=True,
        is_active=True,
    )
    session.add(location)
    session.flush()
    return warehouse, location


def _ready_inbound(session, *, quantity: int = 2):
    base = create_catalog_and_stock(session)
    staff = _staff_from_base(session, base)
    order_id, order_number, item_ids = create_order_items(session, base, [quantity])
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == order_id)
    )
    package = SellerInboundPackage(
        seller_order_id=seller_order.id,
        package_code=f"PKG-{uuid.uuid4().hex[:10].upper()}",
        barcode="placeholder",
        status=SellerInboundPackageStatus.READY_FOR_DROPOFF,
        ready_for_dropoff_at=datetime.now(timezone.utc),
        ready_for_dropoff_by_user_id=staff.id,
    )
    package.barcode = package.package_code
    session.add(package)
    session.flush()
    session.add(
        SellerInboundPackageItem(
            package_id=package.id,
            order_item_id=item_ids[0],
            quantity=quantity,
        )
    )
    session.flush()
    return base, staff, package, session.get(OrderItem, item_ids[0]), order_number


def test_scanner_routes_require_staff_and_real_navigation(session, client):
    assert client.get("/admin/scanner").status_code == 302
    base = create_catalog_and_stock(session)
    buyer = session.get(User, base.buyer_id)
    session.commit()
    _login(client, buyer)
    assert client.get("/admin/scanner").status_code == 403

    staff = session.get(User, base.operator_id)
    staff.is_ecuvel_staff = True
    session.commit()
    _login(client, staff)
    response = client.get("/admin/scanner")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Recepción en punto" in body
    assert "Entrega a transportista" in body
    assert "Recepción de traslado" in body
    assert "Entrega al cliente" in body
    assert "Create Shipment" not in body
    for path in (
        "/admin/scanner/receive",
        "/admin/scanner/transport",
        "/admin/scanner/arrival",
        "/admin/scanner/handover",
        "/admin/scanner/package",
    ):
        assert client.get(path).status_code == 200


def test_operating_point_is_saved_in_staff_session(session, client):
    base = create_catalog_and_stock(session)
    staff = _staff_from_base(session, base)
    session.commit()
    _login(client, staff)
    response = client.post(
        "/admin/scanner/context",
        data={"warehouse_id": str(base.warehouse_id), "return_mode": "home"},
    )
    assert response.status_code == 302
    with client.session_transaction() as browser:
        assert browser["admin_operating_warehouse_id"] == str(base.warehouse_id)


def test_reception_requires_exact_multiset_and_current_point(session, client):
    base, staff, package, item, _order_number = _ready_inbound(session, quantity=2)
    session.commit()
    _login(client, staff)
    _set_point(client, base.warehouse_id)
    page = client.get(f"/admin/scanner/receive?code={package.package_code}")
    assert page.status_code == 200
    assert item.seller_sku_snapshot in page.get_data(as_text=True)

    incomplete = client.post(
        "/admin/scanner/receive",
        data={
            "idempotency_key": "receive-incomplete",
            "package_code": package.package_code,
            "received_location_id": str(base.receiving_location_id),
            "verified_product_codes": item.seller_sku_snapshot,
        },
    )
    assert incomplete.status_code == 302
    session.expire_all()
    assert session.get(SellerInboundPackage, package.id).status == SellerInboundPackageStatus.READY_FOR_DROPOFF

    complete = client.post(
        "/admin/scanner/receive",
        data=MultiDict(
            [
                ("idempotency_key", "receive-complete"),
                ("package_code", package.package_code),
                ("received_location_id", str(base.receiving_location_id)),
                ("verified_product_codes", item.seller_sku_snapshot),
                ("verified_product_codes", item.seller_sku_snapshot),
            ]
        ),
    )
    assert complete.status_code == 302
    session.expire_all()
    stored = session.get(SellerInboundPackage, package.id)
    assert stored.status == SellerInboundPackageStatus.RECEIVED_BY_ECUVEL
    state = session.scalar(
        select(LogisticsPackageState).where(
            LogisticsPackageState.seller_inbound_package_id == package.id
        )
    )
    assert state.current_warehouse_id == base.warehouse_id
    assert state.custodian_warehouse_id == base.warehouse_id


def test_reception_rejects_location_from_another_point(session, client):
    base, staff, package, item, _order_number = _ready_inbound(session, quantity=1)
    _other_point, other_location = _point(session, "Punto ajeno")
    session.commit()
    _login(client, staff)
    _set_point(client, base.warehouse_id)
    response = client.post(
        "/admin/scanner/receive",
        data={
            "idempotency_key": "wrong-point",
            "package_code": package.package_code,
            "received_location_id": str(other_location.id),
            "verified_product_codes": item.seller_sku_snapshot,
        },
    )
    assert response.status_code == 302
    session.expire_all()
    assert session.get(SellerInboundPackage, package.id).status == SellerInboundPackageStatus.READY_FOR_DROPOFF


def test_transport_pickup_revalidates_point_before_transferring_custody(session, client):
    base, staff, package, item, _order_number = _ready_inbound(session, quantity=1)
    from app.services.seller_inbound_packages import receive_seller_inbound_package

    receive_seller_inbound_package(
        session,
        package_code=package.package_code,
        received_location_id=base.receiving_location_id,
        actor_user_id=staff.id,
        verified_product_codes=(item.seller_sku_snapshot,),
        expected_warehouse_id=base.warehouse_id,
    )
    destination, _destination_location = _point(session, "Destino")
    wrong_point, _wrong_location = _point(session, "Punto ajeno")
    assign_package_transfer(
        session,
        package_code=package.package_code,
        destination_warehouse_id=destination.id,
        responsible_user_id=staff.id,
        actor_user_id=staff.id,
        idempotency_key="assign-transport-route",
    )
    session.commit()
    _login(client, staff)
    _set_point(client, wrong_point.id)
    rejected = client.post(
        "/admin/scanner/transport",
        data={
            "idempotency_key": "pickup-wrong-point",
            "package_code": package.package_code,
        },
    )
    assert rejected.status_code == 302
    session.expire_all()
    state = session.scalar(
        select(LogisticsPackageState).where(
            LogisticsPackageState.seller_inbound_package_id == package.id
        )
    )
    assert state.status == LogisticsPackageStatus.ASSIGNED
    assert state.custodian_warehouse_id == base.warehouse_id

    _set_point(client, base.warehouse_id)
    accepted = client.post(
        "/admin/scanner/transport",
        data={
            "idempotency_key": "pickup-correct-point",
            "package_code": package.package_code,
        },
    )
    assert accepted.status_code == 302
    session.expire_all()
    state = session.scalar(
        select(LogisticsPackageState).where(
            LogisticsPackageState.seller_inbound_package_id == package.id
        )
    )
    assert state.status == LogisticsPackageStatus.IN_TRANSIT
    assert state.custodian_user_id == staff.id


def test_wrong_transfer_destination_creates_real_deviation(session, client):
    base, staff, package, item, _order_number = _ready_inbound(session, quantity=1)
    # Establish the canonical inbound state first.
    from app.services.seller_inbound_packages import receive_seller_inbound_package

    receive_seller_inbound_package(
        session,
        package_code=package.package_code,
        received_location_id=base.receiving_location_id,
        actor_user_id=staff.id,
        verified_product_codes=(item.seller_sku_snapshot,),
        expected_warehouse_id=base.warehouse_id,
    )
    destination, _destination_location = _point(session, "Destino esperado")
    wrong_point, wrong_location = _point(session, "Punto equivocado")
    assign_package_transfer(
        session,
        package_code=package.package_code,
        destination_warehouse_id=destination.id,
        responsible_user_id=staff.id,
        actor_user_id=staff.id,
        idempotency_key="assign-scanner-test",
    )
    confirm_package_pickup(
        session,
        package_code=package.package_code,
        actor_user_id=staff.id,
        expected_origin_warehouse_id=base.warehouse_id,
        idempotency_key="pickup-scanner-test",
    )
    session.commit()
    _login(client, staff)
    _set_point(client, wrong_point.id)
    response = client.post(
        "/admin/scanner/arrival",
        data={
            "idempotency_key": "wrong-arrival",
            "package_code": package.package_code,
            "received_location_id": str(wrong_location.id),
        },
    )
    assert response.status_code == 302
    session.expire_all()
    state = session.scalar(
        select(LogisticsPackageState).where(
            LogisticsPackageState.seller_inbound_package_id == package.id
        )
    )
    assert state.status == LogisticsPackageStatus.DEVIATED
    assert state.is_deviated
    assert state.current_warehouse_id == wrong_point.id
    assert state.expected_destination_warehouse_id == destination.id


def test_customer_handover_requires_identity_and_all_packages(session, client):
    base = create_catalog_and_stock(session)
    staff = _staff_from_base(session, base)
    ready = create_ready_for_pickup_order(session, base, [1, 1])
    buyer = session.get(User, base.buyer_id)
    session.commit()
    _login(client, staff)
    _set_point(client, base.warehouse_id)
    page = client.get(
        f"/admin/scanner/handover?buyer={buyer.public_account_code}&order_number={ready.order_number}"
    )
    assert page.status_code == 200
    assert ready.order_number in page.get_data(as_text=True)

    without_identity = client.post(
        "/admin/scanner/handover",
        data=MultiDict(
            [
                ("idempotency_key", "handover-no-id"),
                ("buyer_code", buyer.public_account_code),
                ("order_number", ready.order_number),
                *(('scanned_codes', code) for code in ready.package_codes),
            ]
        ),
    )
    assert without_identity.status_code == 302
    session.expire_all()
    assert all(
        package.status == PackageStatus.READY_FOR_PICKUP
        for package in session.scalars(
            select(OrderPackage).where(OrderPackage.package_code.in_(ready.package_codes))
        )
    )

    delivered = client.post(
        "/admin/scanner/handover",
        data=MultiDict(
            [
                ("idempotency_key", "handover-complete"),
                ("buyer_code", buyer.public_account_code),
                ("order_number", ready.order_number),
                ("identity_confirmed", "1"),
                *(('scanned_codes', code) for code in ready.package_codes),
            ]
        ),
    )
    assert delivered.status_code == 302
    session.expire_all()
    assert all(
        package.status == PackageStatus.HANDED_OVER
        for package in session.scalars(
            select(OrderPackage).where(OrderPackage.package_code.in_(ready.package_codes))
        )
    )


def test_quick_lookup_recognizes_inbound_and_outbound_without_mutation(session):
    base, _staff, inbound, _item, _order_number = _ready_inbound(session, quantity=1)
    ready = create_ready_for_pickup_order(session, base, [1])
    before_inbound = inbound.status
    inbound_view = get_admin_package_lookup(session, code=inbound.package_code)
    outbound_view = get_admin_package_lookup(session, code=ready.package_codes[0])
    assert inbound_view.kind == "inbound"
    assert outbound_view.kind == "outbound"
    assert inbound.status == before_inbound
    outbound = session.scalar(
        select(OrderPackage).where(OrderPackage.package_code == ready.package_codes[0])
    )
    assert outbound.status == PackageStatus.READY_FOR_PICKUP
