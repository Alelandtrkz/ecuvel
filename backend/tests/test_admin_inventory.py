from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import (
    LogisticsPackageState,
    PhysicalInventoryCount,
    PhysicalInventoryCountScan,
    SellerInboundPackage,
    SellerInboundPackageItem,
    SellerOrder,
    User,
)
from app.models.enums import (
    LogisticsPackageStatus,
    SellerInboundPackageStatus,
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


def _received_inbound(session, base, staff, *, suffix: str | None = None):
    _order_id, order_number, item_ids = create_order_items(session, base, [1])
    seller_order = session.scalar(
        select(SellerOrder).where(SellerOrder.order_id == _order_id)
    )
    now = datetime.now(timezone.utc)
    code = f"PKG-{(suffix or uuid.uuid4().hex[:10]).upper()}"
    package = SellerInboundPackage(
        seller_order_id=seller_order.id,
        package_code=code,
        barcode=code,
        status=SellerInboundPackageStatus.RECEIVED_BY_ECUVEL,
        ready_for_dropoff_at=now,
        ready_for_dropoff_by_user_id=staff.id,
        received_at=now,
        received_by_user_id=staff.id,
        received_location_id=base.receiving_location_id,
    )
    session.add(package)
    session.flush()
    session.add(
        SellerInboundPackageItem(
            package_id=package.id,
            order_item_id=item_ids[0],
            quantity=1,
        )
    )
    state = LogisticsPackageState(
        seller_inbound_package_id=package.id,
        status=LogisticsPackageStatus.AT_POINT,
        current_warehouse_id=base.warehouse_id,
        current_location_id=base.receiving_location_id,
        custodian_warehouse_id=base.warehouse_id,
        custodian_user_id=None,
        expected_destination_warehouse_id=None,
        is_deviated=False,
        last_event_at=now,
    )
    session.add(state)
    session.flush()
    return package, state, order_number


def test_inventory_navigation_requires_staff_and_uses_shared_point(session, client):
    base = create_catalog_and_stock(session)
    buyer = session.get(User, base.buyer_id)
    session.commit()
    _login(client, buyer)
    assert client.get("/admin/inventory").status_code == 403

    staff = session.get(User, base.operator_id)
    staff.is_ecuvel_staff = True
    session.commit()
    _login(client, staff)
    response = client.post(
        "/admin/inventory/context",
        data={"warehouse_id": str(base.warehouse_id)},
    )
    assert response.status_code == 302
    with client.session_transaction() as browser:
        assert browser["admin_operating_warehouse_id"] == str(base.warehouse_id)


def test_inventory_page_separates_physical_packages_and_commercial_stock(session, client):
    base = create_catalog_and_stock(session, stock=17)
    staff = session.get(User, base.operator_id)
    staff.is_ecuvel_staff = True
    package, _state, _order_number = _received_inbound(session, base, staff)
    ready = create_ready_for_pickup_order(session, base, [1])
    session.commit()
    _login(client, staff)
    _set_point(client, base.warehouse_id)

    packages = client.get("/admin/inventory")
    assert packages.status_code == 200
    body = packages.get_data(as_text=True)
    assert package.package_code in body
    assert ready.package_codes[0] in body
    assert "Listos para retirar" in body

    stock = client.get("/admin/inventory/stock")
    assert stock.status_code == 200
    stock_body = stock.get_data(as_text=True)
    assert "Product Test" in stock_body
    assert ">16<" in stock_body


def test_inventory_get_routes_do_not_mutate_package_state(session, client):
    base = create_catalog_and_stock(session)
    staff = session.get(User, base.operator_id)
    staff.is_ecuvel_staff = True
    package, state, _ = _received_inbound(session, base, staff)
    session.commit()
    before = (
        state.current_warehouse_id,
        state.current_location_id,
        state.custodian_warehouse_id,
        state.custodian_user_id,
        state.status,
    )
    _login(client, staff)
    _set_point(client, base.warehouse_id)
    for path in (
        "/admin/inventory",
        "/admin/inventory/expected",
        "/admin/inventory/stock",
        "/admin/inventory/movements",
        "/admin/inventory/counts",
    ):
        assert client.get(path).status_code == 200
    session.expire_all()
    stored = session.get(LogisticsPackageState, state.id)
    assert (
        stored.current_warehouse_id,
        stored.current_location_id,
        stored.custodian_warehouse_id,
        stored.custodian_user_id,
        stored.status,
    ) == before


def test_inventory_search_and_count_web_flow_preserve_package_state(session, client):
    base = create_catalog_and_stock(session)
    staff = session.get(User, base.operator_id)
    staff.is_ecuvel_staff = True
    package, state, _ = _received_inbound(session, base, staff)
    session.commit()
    before = (
        state.current_warehouse_id,
        state.current_location_id,
        state.custodian_warehouse_id,
        state.status,
    )
    _login(client, staff)
    _set_point(client, base.warehouse_id)

    search = client.get("/admin/inventory?q=Buyer+Test")
    assert search.status_code == 200
    assert package.package_code in search.get_data(as_text=True)

    started = client.post("/admin/inventory/counts/start")
    assert started.status_code == 302
    count_id = uuid.UUID(started.headers["Location"].rstrip("/").split("/")[-1])

    scanned = client.post(
        f"/admin/inventory/counts/{count_id}/scan",
        data={"package_code": package.package_code},
    )
    assert scanned.status_code == 302
    assert session.scalar(
        select(PhysicalInventoryCountScan).where(
            PhysicalInventoryCountScan.count_id == count_id
        )
    ).classification == "EXPECTED"

    finalized = client.post(
        f"/admin/inventory/counts/{count_id}/finalize",
        data={"confirmed": "1"},
    )
    assert finalized.status_code == 302
    session.expire_all()
    assert session.get(PhysicalInventoryCount, count_id).status == "FINALIZED"
    stored = session.get(LogisticsPackageState, state.id)
    assert (
        stored.current_warehouse_id,
        stored.current_location_id,
        stored.custodian_warehouse_id,
        stored.status,
    ) == before
