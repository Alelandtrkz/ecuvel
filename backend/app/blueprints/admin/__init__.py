from __future__ import annotations

import base64
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session as browser_session,
    url_for,
)
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db, limiter
from app.services.admin_access import ecuvel_staff_required
from app.services.admin_navigation import (
    ADMIN_NAVIGATION,
    ADMIN_SECONDARY_NAVIGATION,
    find_admin_navigation_item,
)
from app.services.admin_fulfillment import (
    get_admin_fulfillment_detail,
    get_admin_fulfillment_page,
)
from app.services.admin_scanner import (
    AdminScannerError,
    get_admin_customer_handover,
    get_admin_inbound_reception,
    get_admin_package_lookup,
    get_admin_scanner_home,
    get_admin_transfer_reception,
    get_admin_transport_pickup,
    normalize_scanned_code,
    require_active_operating_point,
    require_receiving_location,
)
from app.services.admin_operating_context import (
    ADMIN_OPERATING_WAREHOUSE_SESSION_KEY,
    AdminOperatingContextError,
    require_active_operating_point as require_shared_operating_point,
)
from app.services.admin_inventory import (
    get_admin_count_detail_page,
    get_admin_count_list_page,
    get_admin_expected_page,
    get_admin_inventory_page,
    get_admin_movements_page,
    get_admin_stock_page,
)
from app.services.inventory_counts import (
    PhysicalInventoryCountError,
    finalize_physical_inventory_count,
    normalize_count_code,
    scan_physical_inventory_package,
    start_physical_inventory_count,
)
from app.services.admin_operations import (
    get_admin_operations_page,
    search_admin_records,
)
from app.services.admin_orders import (
    format_ecuador_datetime,
    get_admin_order_detail,
    get_admin_orders_page,
    get_admin_payment_review,
)
from app.services.admin_products import (
    get_admin_product_draft,
    get_admin_products_page,
)
from app.services.payment_proofs import (
    PaymentProofServiceError,
    review_payment_proof,
)
from app.services.barcodes import BarcodeRenderError, render_package_code128_svg
from app.services.logistics_tracking import (
    LogisticsTrackingError,
    assign_package_transfer,
    confirm_package_pickup,
    reassign_package_transfer,
    receive_transfer_at_destination,
    report_logistics_incident,
)
from app.services.fulfillment import (
    FulfillmentServiceError,
    handover_order_packages,
)
from app.services.partner_orders import (
    PartnerOrderConflictError,
    PartnerOrderNotFoundError,
    PartnerOrderValidationError,
)
from app.services.seller_inbound_packages import (
    SellerInboundPackageReceptionAccessError,
    receive_seller_inbound_package,
)
from app.services.private_storage import PrivateStorageError, verify_private_file
from app.services.product_draft_preview import build_product_draft_preview
from app.services.product_drafts import build_product_draft_view
from app.services.product_publication import (
    ProductModerationError,
    normalize_moderation_checklist,
    publish_product_draft,
    record_moderation_decision,
    remove_copied_publication_files,
)


admin = Blueprint("admin", __name__, url_prefix="/admin")
_SCANNER_WAREHOUSE_SESSION_KEY = ADMIN_OPERATING_WAREHOUSE_SESSION_KEY


@admin.app_template_filter("ecuador_datetime")
def ecuador_datetime(value) -> str:
    return format_ecuador_datetime(value)


def _shell_context(section: str) -> dict:
    return {
        "admin_navigation": ADMIN_NAVIGATION,
        "admin_secondary_navigation": ADMIN_SECONDARY_NAVIGATION,
        "current_admin_section": section,
    }


def _scanner_operating_point():
    return get_admin_scanner_home(
        db.session,
        warehouse_id=browser_session.get(_SCANNER_WAREHOUSE_SESSION_KEY),
    )


def _scanner_action_token() -> str:
    token = (request.form.get("idempotency_key") or "").strip()
    if not token or len(token) > 120 or not token.replace("-", "").isalnum():
        raise AdminScannerError(
            "La operación expiró. Identifica nuevamente el paquete."
        )
    return token


def _scanner_codes(field: str, fallback_field: str | None = None) -> tuple[str, ...]:
    values = list(request.form.getlist(field))
    if fallback_field:
        fallback = request.form.get(fallback_field) or ""
        values.extend(re.split(r"[\s,;]+", fallback))
    return tuple(
        code
        for value in values
        if (code := " ".join((value or "").strip().split())[:120])
    )


def _scanner_redirect(endpoint: str, **values):
    return redirect(url_for(endpoint, **{key: value for key, value in values.items() if value}))


def _scanner_mutation_error(endpoint: str, exc: Exception, **values):
    db.session.rollback()
    flash(str(exc), "error")
    return _scanner_redirect(endpoint, **values)


@admin.get("")
@ecuvel_staff_required
def operations():
    page = get_admin_operations_page(
        db.session,
        critical_stock_threshold=current_app.config[
            "ADMIN_CRITICAL_STOCK_THRESHOLD"
        ],
        activity_limit=current_app.config["ADMIN_ACTIVITY_LIMIT"],
    )
    return render_template(
        "admin/operations.html",
        page=page,
        **_shell_context("operations"),
    )


@admin.get("/search")
@limiter.limit("30 per minute")
@ecuvel_staff_required
def search():
    page = search_admin_records(
        db.session,
        query=request.args.get("q", ""),
        limit_per_group=current_app.config["ADMIN_SEARCH_GROUP_LIMIT"],
    )
    return render_template(
        "admin/search.html",
        page=page,
        **_shell_context("search"),
    )


@admin.get("/scanner")
@ecuvel_staff_required
def scanner():
    home = _scanner_operating_point()
    return render_template(
        "admin/scanner.html",
        home=home,
        **_shell_context("scanner"),
    )


@admin.post("/scanner/context")
@limiter.limit("30 per hour")
@ecuvel_staff_required
def scanner_context():
    try:
        warehouse = require_active_operating_point(
            db.session,
            request.form.get("warehouse_id"),
        )
    except AdminScannerError as exc:
        flash(str(exc), "error")
    else:
        browser_session[_SCANNER_WAREHOUSE_SESSION_KEY] = str(warehouse.id)
        browser_session.modified = True
        flash(f"Punto operativo actualizado: {warehouse.name}.", "success")
    endpoint = {
        "receive": "admin.scanner_receive",
        "transport": "admin.scanner_transport",
        "arrival": "admin.scanner_arrival",
        "handover": "admin.scanner_handover",
        "package": "admin.scanner_package",
        "inventory": "admin.inventory",
        "inventory-expected": "admin.inventory_expected",
        "inventory-stock": "admin.inventory_stock",
        "inventory-movements": "admin.inventory_movements",
        "inventory-counts": "admin.inventory_counts",
    }.get(request.form.get("return_mode"), "admin.scanner")
    return _scanner_redirect(
        endpoint,
        code=normalize_scanned_code(request.form.get("code")),
        buyer=normalize_scanned_code(request.form.get("buyer")),
        order_number=" ".join((request.form.get("order_number") or "").strip().split())[:120],
    )


@admin.get("/scanner/receive")
@ecuvel_staff_required
def scanner_receive():
    home = _scanner_operating_point()
    code = normalize_scanned_code(request.args.get("code"))
    view = get_admin_inbound_reception(
        db.session,
        code=code,
        operating_point=home.operating_point,
    ) if code else None
    return render_template(
        "admin/scanner_receive.html",
        home=home,
        code=code,
        view=view,
        lookup_error=("No encontramos el paquete de entrada escaneado." if code and view is None else None),
        result=request.args.get("result"),
        action_token=uuid.uuid4().hex,
        **_shell_context("scanner"),
    )


@admin.post("/scanner/receive")
@limiter.limit("120 per hour")
@ecuvel_staff_required
def scanner_receive_confirm():
    code = normalize_scanned_code(request.form.get("package_code"))
    try:
        warehouse = require_active_operating_point(
            db.session,
            browser_session.get(_SCANNER_WAREHOUSE_SESSION_KEY),
            lock=True,
        )
        location = require_receiving_location(
            db.session,
            warehouse_id=warehouse.id,
            location_id=request.form.get("received_location_id"),
            lock=True,
        )
        _scanner_action_token()
        verified_codes = _scanner_codes(
            "verified_product_codes", "verified_product_codes_text"
        )
        receive_seller_inbound_package(
            db.session,
            package_code=code,
            received_location_id=location.id,
            actor_user_id=current_user.id,
            verified_product_codes=verified_codes,
            expected_warehouse_id=warehouse.id,
        )
        db.session.commit()
    except (
        AdminScannerError,
        PartnerOrderConflictError,
        PartnerOrderNotFoundError,
        PartnerOrderValidationError,
        SellerInboundPackageReceptionAccessError,
        LogisticsTrackingError,
    ) as exc:
        return _scanner_mutation_error("admin.scanner_receive", exc, code=code)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falló recepción Scanner package=%s", code)
        flash("No pudimos registrar la recepción del paquete.", "error")
        return _scanner_redirect("admin.scanner_receive", code=code)
    flash("Paquete recibido correctamente.", "success")
    return _scanner_redirect("admin.scanner_receive", code=code, result="received")


@admin.get("/scanner/transport")
@ecuvel_staff_required
def scanner_transport():
    home = _scanner_operating_point()
    code = normalize_scanned_code(request.args.get("code"))
    view = get_admin_transport_pickup(
        db.session,
        code=code,
        operating_point=home.operating_point,
    ) if code else None
    return render_template(
        "admin/scanner_transport.html",
        home=home,
        code=code,
        view=view,
        lookup_error=("No encontramos un paquete de entrada con ese código." if code and view is None else None),
        result=request.args.get("result"),
        action_token=uuid.uuid4().hex,
        **_shell_context("scanner"),
    )


@admin.post("/scanner/transport")
@limiter.limit("120 per hour")
@ecuvel_staff_required
def scanner_transport_confirm():
    code = normalize_scanned_code(request.form.get("package_code"))
    try:
        warehouse = require_active_operating_point(
            db.session,
            browser_session.get(_SCANNER_WAREHOUSE_SESSION_KEY),
            lock=True,
        )
        token = _scanner_action_token()
        confirm_package_pickup(
            db.session,
            package_code=code,
            actor_user_id=current_user.id,
            expected_origin_warehouse_id=warehouse.id,
            notes=request.form.get("notes"),
            idempotency_key=f"scanner:pickup:{token}",
        )
        db.session.commit()
    except (AdminScannerError, LogisticsTrackingError) as exc:
        return _scanner_mutation_error("admin.scanner_transport", exc, code=code)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falló salida Scanner package=%s", code)
        flash("No pudimos confirmar la salida del paquete.", "error")
        return _scanner_redirect("admin.scanner_transport", code=code)
    flash("Salida confirmada y custodia transferida al responsable.", "success")
    return _scanner_redirect("admin.scanner_transport", code=code, result="departed")


@admin.get("/scanner/arrival")
@ecuvel_staff_required
def scanner_arrival():
    home = _scanner_operating_point()
    code = normalize_scanned_code(request.args.get("code"))
    view = get_admin_transfer_reception(
        db.session,
        code=code,
        operating_point=home.operating_point,
    ) if code else None
    return render_template(
        "admin/scanner_arrival.html",
        home=home,
        code=code,
        view=view,
        lookup_error=("No encontramos un paquete en traslado con ese código." if code and view is None else None),
        result=request.args.get("result"),
        action_token=uuid.uuid4().hex,
        **_shell_context("scanner"),
    )


@admin.post("/scanner/arrival")
@limiter.limit("120 per hour")
@ecuvel_staff_required
def scanner_arrival_confirm():
    code = normalize_scanned_code(request.form.get("package_code"))
    try:
        warehouse = require_active_operating_point(
            db.session,
            browser_session.get(_SCANNER_WAREHOUSE_SESSION_KEY),
            lock=True,
        )
        token = _scanner_action_token()
        operation = request.form.get("operation", "accept")
        if operation == "reject":
            report_logistics_incident(
                db.session,
                package_code=code,
                warehouse_id=warehouse.id,
                actor_user_id=current_user.id,
                reason=request.form.get("notes") or "Destino operativo incorrecto",
                idempotency_key=f"scanner:arrival-rejected:{token}",
            )
            was_deviated = False
            was_rejected = True
        else:
            location = require_receiving_location(
                db.session,
                warehouse_id=warehouse.id,
                location_id=request.form.get("received_location_id"),
                lock=True,
            )
            result = receive_transfer_at_destination(
                db.session,
                package_code=code,
                warehouse_id=warehouse.id,
                location_id=location.id,
                actor_user_id=current_user.id,
                notes=request.form.get("notes"),
                idempotency_key=f"scanner:arrival:{token}",
            )
            was_deviated = result.package_state.is_deviated
            was_rejected = False
        db.session.commit()
    except (AdminScannerError, LogisticsTrackingError) as exc:
        return _scanner_mutation_error("admin.scanner_arrival", exc, code=code)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falló llegada Scanner package=%s", code)
        flash("No pudimos registrar la llegada del paquete.", "error")
        return _scanner_redirect("admin.scanner_arrival", code=code)
    if was_rejected:
        flash(
            "Intento registrado. La custodia continúa con el transportista.",
            "warning",
        )
    else:
        flash(
            "Paquete recibido y marcado como desviado."
            if was_deviated
            else "Paquete recibido en el destino correcto.",
            "warning" if was_deviated else "success",
        )
    return _scanner_redirect(
        "admin.scanner_arrival",
        code=code,
        result=("rejected" if was_rejected else ("deviated" if was_deviated else "arrived")),
    )


@admin.get("/scanner/handover")
@ecuvel_staff_required
def scanner_handover():
    home = _scanner_operating_point()
    buyer_code = normalize_scanned_code(request.args.get("buyer"))
    order_number = " ".join((request.args.get("order_number") or "").strip().split())[:120]
    view = get_admin_customer_handover(
        db.session,
        buyer_code=buyer_code,
        operating_point=home.operating_point,
        order_number=order_number,
    ) if buyer_code else None
    return render_template(
        "admin/scanner_handover.html",
        home=home,
        buyer_code=buyer_code,
        order_number=order_number,
        view=view,
        lookup_error=("No encontramos un cliente con ese código público." if buyer_code and view is None else None),
        result=request.args.get("result"),
        completed_order=request.args.get("completed_order"),
        action_token=uuid.uuid4().hex,
        **_shell_context("scanner"),
    )


@admin.post("/scanner/handover")
@limiter.limit("60 per hour")
@ecuvel_staff_required
def scanner_handover_confirm():
    buyer_code = normalize_scanned_code(request.form.get("buyer_code"))
    order_number = " ".join((request.form.get("order_number") or "").strip().split())[:120]
    try:
        warehouse = require_active_operating_point(
            db.session,
            browser_session.get(_SCANNER_WAREHOUSE_SESSION_KEY),
            lock=True,
        )
        _scanner_action_token()
        if request.form.get("identity_confirmed") != "1":
            raise AdminScannerError(
                "Confirma que verificaste físicamente la identidad del cliente."
            )
        view = get_admin_customer_handover(
            db.session,
            buyer_code=buyer_code,
            operating_point=get_admin_scanner_home(
                db.session, warehouse_id=warehouse.id
            ).operating_point,
            order_number=order_number,
        )
        if view is None or view.selected_order is None:
            raise AdminScannerError(
                "El pedido no está listo para retirarse en este punto."
            )
        scanned_codes = _scanner_codes("scanned_codes", "scanned_codes_text")
        handover_order_packages(
            session=db.session,
            order_number=view.selected_order.order_number,
            scanned_codes=scanned_codes,
            actor_user_id=current_user.id,
            expected_warehouse_id=warehouse.id,
            notes=request.form.get("notes"),
        )
        db.session.commit()
    except (AdminScannerError, FulfillmentServiceError, ValueError) as exc:
        return _scanner_mutation_error(
            "admin.scanner_handover",
            exc,
            buyer=buyer_code,
            order_number=order_number,
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falló handover Scanner order=%s", order_number)
        flash("No pudimos confirmar la entrega del pedido.", "error")
        return _scanner_redirect(
            "admin.scanner_handover", buyer=buyer_code, order_number=order_number
        )
    flash("Pedido entregado correctamente.", "success")
    return _scanner_redirect(
        "admin.scanner_handover",
        buyer=buyer_code,
        result="handed-over",
        completed_order=view.selected_order.order_number,
    )


@admin.get("/scanner/package")
@ecuvel_staff_required
def scanner_package():
    home = _scanner_operating_point()
    code = normalize_scanned_code(request.args.get("code"))
    view = get_admin_package_lookup(db.session, code=code) if code else None
    return render_template(
        "admin/scanner_package_lookup.html",
        home=home,
        code=code,
        view=view,
        lookup_error=("No encontramos un paquete con ese código." if code and view is None else None),
        **_shell_context("scanner"),
    )


def _inventory_warehouse_id():
    return browser_session.get(ADMIN_OPERATING_WAREHOUSE_SESSION_KEY)


@admin.post("/inventory/context")
@limiter.limit("30 per hour")
@ecuvel_staff_required
def inventory_context():
    try:
        warehouse = require_shared_operating_point(
            db.session, request.form.get("warehouse_id")
        )
    except AdminOperatingContextError as exc:
        flash(str(exc), "error")
    else:
        browser_session[ADMIN_OPERATING_WAREHOUSE_SESSION_KEY] = str(warehouse.id)
        browser_session.modified = True
        flash(f"Punto operativo actualizado: {warehouse.name}.", "success")
    destination = {
        "expected": "admin.inventory_expected",
        "stock": "admin.inventory_stock",
        "movements": "admin.inventory_movements",
        "counts": "admin.inventory_counts",
    }.get(request.form.get("return_view"), "admin.inventory")
    return redirect(url_for(destination))


@admin.get("/inventory")
@ecuvel_staff_required
def inventory():
    page = get_admin_inventory_page(
        db.session,
        warehouse_id=_inventory_warehouse_id(),
        query=request.args.get("q"),
        active_filter=request.args.get("status"),
        page=request.args.get("page"),
    )
    return render_template(
        "admin/inventory.html", page=page, **_shell_context("inventory")
    )


@admin.get("/inventory/expected")
@ecuvel_staff_required
def inventory_expected():
    page = get_admin_expected_page(
        db.session,
        warehouse_id=_inventory_warehouse_id(),
        page=request.args.get("page"),
    )
    return render_template(
        "admin/inventory_expected.html",
        page=page,
        **_shell_context("inventory"),
    )


@admin.get("/inventory/stock")
@ecuvel_staff_required
def inventory_stock():
    page = get_admin_stock_page(
        db.session,
        warehouse_id=_inventory_warehouse_id(),
        query=request.args.get("q"),
        page=request.args.get("page"),
    )
    return render_template(
        "admin/inventory_stock.html", page=page, **_shell_context("inventory")
    )


@admin.get("/inventory/movements")
@ecuvel_staff_required
def inventory_movements():
    page = get_admin_movements_page(
        db.session,
        warehouse_id=_inventory_warehouse_id(),
        active_filter=request.args.get("type"),
        page=request.args.get("page"),
    )
    return render_template(
        "admin/inventory_movements.html",
        page=page,
        **_shell_context("inventory"),
    )


@admin.get("/inventory/counts")
@ecuvel_staff_required
def inventory_counts():
    page = get_admin_count_list_page(
        db.session, warehouse_id=_inventory_warehouse_id()
    )
    return render_template(
        "admin/inventory_counts.html", page=page, **_shell_context("inventory")
    )


@admin.post("/inventory/counts/start")
@limiter.limit("20 per hour")
@ecuvel_staff_required
def inventory_count_start():
    try:
        warehouse = require_shared_operating_point(
            db.session, _inventory_warehouse_id(), lock=True
        )
        count = start_physical_inventory_count(
            db.session,
            warehouse_id=warehouse.id,
            location_id=request.form.get("location_id"),
            actor_user_id=current_user.id,
            notes=request.form.get("notes"),
        )
        db.session.commit()
    except (AdminOperatingContextError, PhysicalInventoryCountError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("admin.inventory_counts"))
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falló el inicio del conteo físico")
        flash("No pudimos iniciar el conteo físico.", "error")
        return redirect(url_for("admin.inventory_counts"))
    flash("Conteo físico iniciado con su línea base congelada.", "success")
    return redirect(url_for("admin.inventory_count_detail", count_id=count.id))


@admin.get("/inventory/counts/<uuid:count_id>")
@ecuvel_staff_required
def inventory_count_detail(count_id: uuid.UUID):
    page = get_admin_count_detail_page(
        db.session,
        count_id=count_id,
        warehouse_id=_inventory_warehouse_id(),
    )
    if page is None:
        abort(404)
    return render_template(
        "admin/inventory_count_detail.html",
        page=page,
        **_shell_context("inventory"),
    )


@admin.post("/inventory/counts/<uuid:count_id>/scan")
@limiter.limit("600 per hour")
@ecuvel_staff_required
def inventory_count_scan(count_id: uuid.UUID):
    try:
        warehouse = require_shared_operating_point(
            db.session, _inventory_warehouse_id(), lock=True
        )
        result = scan_physical_inventory_package(
            db.session,
            count_id=count_id,
            warehouse_id=warehouse.id,
            code=normalize_count_code(request.form.get("package_code")),
            actor_user_id=current_user.id,
        )
        db.session.commit()
    except (AdminOperatingContextError, PhysicalInventoryCountError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falló escaneo de conteo count=%s", count_id)
        flash("No pudimos registrar el escaneo.", "error")
    else:
        if result.duplicate:
            flash("Ese paquete ya estaba verificado; el total no cambió.", "warning")
        elif result.scan.classification == "UNEXPECTED":
            flash("Paquete no esperado registrado como hallazgo, sin moverlo.", "warning")
        else:
            flash("Paquete verificado.", "success")
    return redirect(url_for("admin.inventory_count_detail", count_id=count_id))


@admin.post("/inventory/counts/<uuid:count_id>/finalize")
@limiter.limit("20 per hour")
@ecuvel_staff_required
def inventory_count_finalize(count_id: uuid.UUID):
    try:
        if request.form.get("confirmed") != "1":
            raise PhysicalInventoryCountError(
                "Confirma que deseas cerrar el conteo de forma irreversible."
            )
        warehouse = require_shared_operating_point(
            db.session, _inventory_warehouse_id(), lock=True
        )
        finalize_physical_inventory_count(
            db.session,
            count_id=count_id,
            warehouse_id=warehouse.id,
            actor_user_id=current_user.id,
        )
        db.session.commit()
    except (AdminOperatingContextError, PhysicalInventoryCountError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falló cierre de conteo count=%s", count_id)
        flash("No pudimos finalizar el conteo.", "error")
    else:
        flash("Conteo finalizado. Las diferencias quedaron registradas.", "success")
    return redirect(url_for("admin.inventory_count_detail", count_id=count_id))


@admin.get("/orders")
@ecuvel_staff_required
def orders():
    page = get_admin_orders_page(
        db.session,
        tab=request.args.get("status"),
        query=request.args.get("q"),
        payment=request.args.get("payment"),
        fulfillment=request.args.get("fulfillment"),
        attention=request.args.get("attention"),
        date=request.args.get("date"),
        page=request.args.get("page"),
        page_size=request.args.get("page_size"),
    )
    return render_template(
        "admin/orders.html",
        page=page,
        **_shell_context("orders"),
    )


@admin.get("/fulfillment")
@ecuvel_staff_required
def fulfillment():
    page = get_admin_fulfillment_page(
        db.session,
        status=request.args.get("status"),
        query=request.args.get("q"),
        point=request.args.get("point"),
        destination=request.args.get("destination"),
        custodian=request.args.get("custodian"),
        deviated=request.args.get("deviated"),
        age=request.args.get("age"),
        page=request.args.get("page"),
        page_size=request.args.get("page_size"),
    )
    return render_template(
        "admin/fulfillment.html",
        page=page,
        **_shell_context("fulfillment"),
    )


@admin.get("/fulfillment/<string:package_code>")
@ecuvel_staff_required
def fulfillment_detail(package_code: str):
    detail = get_admin_fulfillment_detail(
        db.session,
        package_code=package_code,
    )
    if detail is None:
        abort(404)
    return render_template(
        "admin/fulfillment_detail.html",
        detail=detail,
        action_token=uuid.uuid4().hex,
        **_shell_context("fulfillment"),
    )


def _uuid_field(name: str) -> uuid.UUID:
    try:
        return uuid.UUID((request.form.get(name) or "").strip())
    except (TypeError, ValueError) as exc:
        raise LogisticsTrackingError("La selección enviada no es válida.") from exc


def _optional_datetime_field(name: str) -> datetime | None:
    raw = (request.form.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise LogisticsTrackingError("La fecha estimada no es válida.") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("America/Guayaquil"))
    return value.astimezone(timezone.utc)


def _fulfillment_action(package_code: str, operation: str):
    key = (request.form.get("idempotency_key") or uuid.uuid4().hex).strip()[:120]
    try:
        if operation in {"assign", "correct"}:
            assign_package_transfer(
                db.session,
                package_code=package_code,
                destination_warehouse_id=_uuid_field("destination_warehouse_id"),
                responsible_user_id=_uuid_field("responsible_user_id"),
                actor_user_id=current_user.id,
                vehicle_code=request.form.get("vehicle_code"),
                eta_at=_optional_datetime_field("eta_at"),
                notes=request.form.get("notes"),
                corrective=operation == "correct",
                idempotency_key=f"admin:{operation}:{key}",
            )
        elif operation == "reassign":
            reassign_package_transfer(
                db.session,
                package_code=package_code,
                responsible_user_id=_uuid_field("responsible_user_id"),
                actor_user_id=current_user.id,
                vehicle_code=request.form.get("vehicle_code"),
                notes=request.form.get("notes"),
                idempotency_key=f"admin:{operation}:{key}",
            )
        elif operation == "pickup":
            confirm_package_pickup(
                db.session,
                package_code=package_code,
                actor_user_id=current_user.id,
                notes=request.form.get("notes"),
                idempotency_key=f"admin:{operation}:{key}",
            )
        elif operation == "receive":
            receive_transfer_at_destination(
                db.session,
                package_code=package_code,
                warehouse_id=_uuid_field("warehouse_id"),
                actor_user_id=current_user.id,
                notes=request.form.get("notes"),
                idempotency_key=f"admin:{operation}:{key}",
            )
        else:
            abort(404)
        db.session.commit()
    except LogisticsTrackingError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falló acción fulfillment operation=%s package=%s",
            operation,
            package_code,
        )
        flash("No pudimos guardar el movimiento logístico.", "error")
    else:
        flash("Movimiento logístico guardado correctamente.", "success")
    return redirect(
        url_for("admin.fulfillment_detail", package_code=package_code.upper())
    )


@admin.post("/fulfillment/<string:package_code>/assign")
@limiter.limit("60 per hour")
@ecuvel_staff_required
def fulfillment_assign(package_code: str):
    return _fulfillment_action(package_code, "assign")


@admin.post("/fulfillment/<string:package_code>/correct")
@limiter.limit("60 per hour")
@ecuvel_staff_required
def fulfillment_correct(package_code: str):
    return _fulfillment_action(package_code, "correct")


@admin.post("/fulfillment/<string:package_code>/reassign")
@limiter.limit("60 per hour")
@ecuvel_staff_required
def fulfillment_reassign(package_code: str):
    return _fulfillment_action(package_code, "reassign")


@admin.post("/fulfillment/<string:package_code>/pickup")
@limiter.limit("60 per hour")
@ecuvel_staff_required
def fulfillment_pickup(package_code: str):
    return _fulfillment_action(package_code, "pickup")


@admin.post("/fulfillment/<string:package_code>/receive")
@limiter.limit("60 per hour")
@ecuvel_staff_required
def fulfillment_receive(package_code: str):
    return _fulfillment_action(package_code, "receive")


@admin.get("/fulfillment/<string:package_code>/label")
@ecuvel_staff_required
def fulfillment_label(package_code: str):
    detail = get_admin_fulfillment_detail(db.session, package_code=package_code)
    if detail is None:
        abort(404)
    try:
        barcode_svg = render_package_code128_svg(detail.barcode)
    except BarcodeRenderError as exc:
        return Response(str(exc), status=422, mimetype="text/plain")
    return render_template(
        "admin/fulfillment_label.html",
        detail=detail,
        barcode_data=base64.b64encode(barcode_svg).decode("ascii"),
    )


@admin.get("/orders/<string:order_number>")
@ecuvel_staff_required
def order_detail(order_number: str):
    detail = get_admin_order_detail(db.session, order_number=order_number)
    if detail is None:
        abort(404)
    return render_template(
        "admin/order_detail.html",
        detail=detail,
        **_shell_context("orders"),
    )


def _payment_review_or_404(order_number: str):
    review = get_admin_payment_review(
        db.session,
        order_number=order_number,
        bank_name=current_app.config.get("BANK_TRANSFER_BANK_NAME"),
        account_suffix=current_app.config.get("BANK_TRANSFER_ACCOUNT_LAST4"),
    )
    if review is None:
        abort(404)
    return review


@admin.get("/orders/<string:order_number>/payment")
@ecuvel_staff_required
def payment_review(order_number: str):
    review = _payment_review_or_404(order_number)
    return render_template(
        "admin/payment_review.html",
        review=review,
        **_shell_context("orders"),
    )


@admin.get("/orders/<string:order_number>/payment-proof")
@ecuvel_staff_required
def payment_proof(order_number: str):
    review = _payment_review_or_404(order_number)
    proof_id = review.payment.proof_id
    if proof_id is None:
        abort(404)
    from app.models import PaymentProof

    proof = db.session.get(PaymentProof, proof_id)
    if proof is None or proof.media_type not in current_app.config[
        "PAYMENT_PROOF_ALLOWED_MEDIA_TYPES"
    ]:
        abort(404)
    try:
        path = verify_private_file(
            root=current_app.config["PAYMENT_PROOF_UPLOAD_DIR"],
            storage_key=proof.storage_key,
            size_bytes=proof.size_bytes,
            sha256=proof.sha256,
        )
    except PrivateStorageError:
        abort(404)
    response = send_file(
        path,
        mimetype=proof.media_type,
        as_attachment=request.args.get("download") == "1",
        download_name=proof.original_filename,
        conditional=False,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _review_payment(order_number: str, decision: str):
    review = _payment_review_or_404(order_number)
    if not review.payment.can_review or review.payment.proof_id is None:
        flash("Este comprobante ya no admite decisiones.", "error")
        return redirect(url_for("admin.payment_review", order_number=order_number))
    reason = request.form.get("reason")
    notes = request.form.get("notes")
    try:
        result = review_payment_proof(
            session=db.session,
            proof_id=review.payment.proof_id,
            decision=decision,
            reviewer_user_id=current_user.id,
            storage_root=current_app.config["PAYMENT_PROOF_UPLOAD_DIR"],
            reason=reason,
            notes=notes,
        )
        db.session.commit()
    except PaymentProofServiceError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falló la decisión web del pago order_number=%s", order_number
        )
        flash("No pudimos guardar la decisión. Inténtalo nuevamente.", "error")
    else:
        flash(
            "Pago aprobado correctamente."
            if result.proof_status.value == "APPROVED"
            else "Pago rechazado correctamente.",
            "success",
        )
    return redirect(url_for("admin.payment_review", order_number=order_number))


@admin.post("/orders/<string:order_number>/payment/approve")
@limiter.limit("20 per hour")
@ecuvel_staff_required
def approve_payment(order_number: str):
    return _review_payment(order_number, "approve")


@admin.post("/orders/<string:order_number>/payment/reject")
@limiter.limit("20 per hour")
@ecuvel_staff_required
def reject_payment(order_number: str):
    return _review_payment(order_number, "reject")


def _draft_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


@admin.get("/products")
@ecuvel_staff_required
def products():
    page = get_admin_products_page(
        db.session,
        status_key=request.args.get("status", "review"),
        query=request.args.get("q", ""),
        page=request.args.get("page", 1, type=int) or 1,
        selected_id=_draft_uuid(request.args.get("draft")),
    )
    return render_template(
        "admin/products.html",
        page=page,
        **_shell_context("products"),
    )


@admin.get("/products/<uuid:draft_id>/preview")
@ecuvel_staff_required
def product_preview(draft_id: uuid.UUID):
    draft = get_admin_product_draft(db.session, draft_id)
    if draft is None:
        abort(404)
    draft_view = build_product_draft_view(draft)
    preview = build_product_draft_preview(
        draft_view,
        requested_sku=request.args.get("variant"),
        selected_view="storefront",
        media_endpoint="admin.product_file",
    )
    return render_template(
        "admin/product_preview.html",
        draft=draft,
        draft_view=draft_view,
        preview=preview,
        **_shell_context("products"),
    )


@admin.get("/products/<uuid:draft_id>/files/<uuid:file_id>")
@ecuvel_staff_required
def product_file(draft_id: uuid.UUID, file_id: uuid.UUID):
    draft = get_admin_product_draft(db.session, draft_id)
    if draft is None:
        abort(404)
    file_record = next((item for item in draft.files if item.id == file_id), None)
    if file_record is None or file_record.status.value != "ACTIVE":
        abort(404)
    try:
        path = verify_private_file(
            root=current_app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"],
            storage_key=file_record.storage_key,
            size_bytes=file_record.size_bytes,
            sha256=file_record.sha256,
        )
    except PrivateStorageError:
        abort(404)
    response = send_file(
        path,
        mimetype=file_record.media_type,
        as_attachment=request.args.get("download") == "1",
        download_name=file_record.original_filename,
        conditional=False,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _moderation_redirect(draft_id: uuid.UUID):
    if request.form.get("source") == "preview":
        return redirect(url_for("admin.product_preview", draft_id=draft_id))
    return redirect(url_for("admin.products", status="review", draft=draft_id))


def _record_product_decision(draft_id: uuid.UUID, decision: str):
    checklist = normalize_moderation_checklist(request.form.getlist("checklist"))
    try:
        record_moderation_decision(
            db.session,
            draft_id=draft_id,
            actor_user_id=current_user.id,
            decision=decision,
            checklist=checklist,
            reason_code=request.form.get("reason_code"),
            note=request.form.get("note"),
        )
        db.session.commit()
    except ProductModerationError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falló la moderación del producto %s", draft_id)
        flash("No pudimos guardar la decisión. Inténtalo nuevamente.", "error")
    else:
        flash(
            "La publicación volvió a la tienda para correcciones."
            if decision == "CHANGES_REQUESTED"
            else "La publicación fue rechazada.",
            "success",
        )
    return _moderation_redirect(draft_id)


@admin.post("/products/<uuid:draft_id>/request-changes")
@limiter.limit("30 per hour")
@ecuvel_staff_required
def request_product_changes(draft_id: uuid.UUID):
    return _record_product_decision(draft_id, "CHANGES_REQUESTED")


@admin.post("/products/<uuid:draft_id>/reject")
@limiter.limit("30 per hour")
@ecuvel_staff_required
def reject_product(draft_id: uuid.UUID):
    return _record_product_decision(draft_id, "REJECTED")


@admin.post("/products/<uuid:draft_id>/approve")
@limiter.limit("30 per hour")
@ecuvel_staff_required
def approve_product(draft_id: uuid.UUID):
    checklist = normalize_moderation_checklist(request.form.getlist("checklist"))
    copied_files = ()
    try:
        result = publish_product_draft(
            db.session,
            draft_id=draft_id,
            actor_user_id=current_user.id,
            checklist=checklist,
            source_media_root=current_app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"],
            catalog_media_root=current_app.config["PRODUCT_CATALOG_MEDIA_DIR"],
        )
        copied_files = result.copied_files
        db.session.commit()
    except ProductModerationError as exc:
        db.session.rollback()
        remove_copied_publication_files(copied_files)
        flash(str(exc), "error")
    except IntegrityError:
        db.session.rollback()
        remove_copied_publication_files(copied_files)
        current_app.logger.info(
            "Conflicto de identificador al publicar el producto %s", draft_id
        )
        flash(
            "No se pudo publicar porque un SKU, código de barras u otro "
            "identificador ya existe en el catálogo.",
            "error",
        )
    except Exception:
        db.session.rollback()
        remove_copied_publication_files(copied_files)
        current_app.logger.exception("Falló la publicación del producto %s", draft_id)
        flash("No pudimos publicar el producto. No se guardó ningún cambio parcial.", "error")
    else:
        flash(
            "El producto ya estaba publicado."
            if result.already_published else "Producto aprobado y publicado correctamente.",
            "success",
        )
        return redirect(url_for("admin.products", status="approved", draft=draft_id))
    return _moderation_redirect(draft_id)


@admin.get("/modules/<string:module_key>")
@ecuvel_staff_required
def module_placeholder(module_key: str):
    item = find_admin_navigation_item(module_key)
    if item is None or item.implemented:
        abort(404)
    return render_template(
        "admin/placeholder.html",
        module=item,
        **_shell_context(module_key),
    )
