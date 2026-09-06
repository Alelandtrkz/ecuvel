from __future__ import annotations

import base64
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import PurePath
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session as browser_session,
    url_for,
)
from flask_login import current_user
from sqlalchemy import select
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
from app.services.admin_payments import (
    AdminPaymentNotFoundError,
    AdminPaymentQueryError,
    get_admin_payment_detail,
    get_admin_payment_kpis,
    list_admin_payments,
)
from app.services.admin_payouts import (
    AdminPayoutNotFoundError,
    AdminPayoutQueryError,
    PAYOUT_STATUS_LABELS,
    PAYOUT_TAB_STATUSES,
    calendar_months,
    cycle_options,
    get_admin_cycle_preview,
    get_admin_payout_detail,
    get_admin_payout_kpis,
    list_admin_payouts,
    list_payout_stores,
)
from app.services.admin_products import (
    commission_snapshot_complete,
    get_admin_product_draft,
    get_admin_products_page,
)
from app.services.admin_reviews import get_admin_review_detail, get_admin_reviews_page
from app.services.admin_stores import (
    APPROVAL_CHECK_LABELS,
    CORRECTION_REASON_LABELS,
    DOCUMENT_CORRECTION_REASON_LABELS,
    FIELD_CORRECTION_REASON_LABELS,
    approve_store_verification,
    contract_status_label,
    document_status_label,
    document_type_label,
    get_admin_store_review,
    get_admin_stores_page,
    request_store_corrections,
)
from app.services.partner_onboarding import (
    PartnerOnboardingError,
    PartnerOnboardingStateError,
    PartnerOnboardingValidationError,
)
from app.services.payment_proofs import (
    PAYMENT_REJECTION_PUBLIC_REASONS,
    PAYMENT_REJECTION_REASON_LABELS,
    InvalidPaymentProofTransitionError,
    PaymentProofExpiredError,
    PaymentProofIntegrityError,
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
from app.services.private_storage import (
    PrivateStorageError,
    delete_private_file,
    private_file_path,
    stage_private_upload,
    verify_private_file,
)
from app.services.product_draft_preview import build_product_draft_preview
from app.services.product_drafts import (
    build_product_draft_view,
    draft_commission_display_rows,
)
from app.services.product_publication import (
    ProductModerationError,
    normalize_moderation_checklist,
    publish_product_draft,
    record_moderation_decision,
    remove_copied_publication_files,
)
from app.services.product_image_processing import product_image_processing_config
from app.services.admin_permissions import (
    ROLE_PERMISSIONS,
    admin_permission_required,
    permissions_for_user,
    user_has_permission,
)
from app.services.bank_accounts import (
    BankAccountAccessError,
    bank_account_summary,
    decrypt_bank_account_for_staff,
    onboarding_bank_account_version,
)
from app.services.financial_audit import (
    BANK_ACCOUNT_SENSITIVE_VIEWED,
    record_financial_audit,
)
from app.services.payout_calendar import PAYOUT_TIMEZONE, payout_cycle_window
from app.services.seller_payouts import (
    SellerPayoutEligibilityError,
    SellerPayoutError,
    SellerPayoutNotFoundError,
    SellerPayoutTransitionError,
    cancel_seller_payout,
    hold_seller_payout,
    mark_seller_payout_paid,
    resume_seller_payout,
    schedule_payout_cycle,
)
from app.services.admin_users import (
    AdminUserError,
    EMPLOYMENT_LABELS,
    OPERATIONAL_LABELS,
    PERMISSION_LABELS,
    STAFF_ROLE_LABELS,
    create_staff_invitation,
    create_staff_member,
    find_staff_by_employee_code,
    find_user_by_public_code,
    get_admin_client_detail,
    get_admin_clients_page,
    get_admin_staff_detail,
    get_admin_staff_page,
    operational_warehouses,
    record_admin_audit,
    revoke_staff_invitations,
    set_staff_access,
    set_user_suspension,
    update_staff_profile,
)
from app.services.authentication import request_password_reset
from app.services.mail import MailError, mail_service
from app.services.transactional_mail import (
    build_mail_action_url,
    password_reset_mail,
    staff_invitation_mail,
)
from app.services.review_moderation import (
    REJECTION_REASON_LABELS,
    ReviewModerationConflictError,
    ReviewModerationError,
    apply_review_moderation_decision,
)
from app.models import PaymentAttempt, PaymentProof, ProductReview, ProductReviewImage, SellerPayout
from app.models.enums import (
    PaymentMethod,
    PaymentProofPrecheckOutcome,
    PaymentProofStatus,
    PaymentStatus,
    ReviewModerationDecisionAction,
    ReviewModerationDecisionSource,
    StaffEmploymentStatus,
    StaffIdentificationType,
    StaffRole,
)


admin = Blueprint("admin", __name__, url_prefix="/admin")
_SCANNER_WAREHOUSE_SESSION_KEY = ADMIN_OPERATING_WAREHOUSE_SESSION_KEY


@admin.app_template_filter("ecuador_datetime")
def ecuador_datetime(value) -> str:
    return format_ecuador_datetime(value)


@admin.app_template_filter("ecuador_date")
def ecuador_date(value) -> str:
    if value is None:
        return ""
    local_value = value.astimezone(PAYOUT_TIMEZONE).date() if isinstance(value, datetime) else value
    months = (
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    )
    return f"{local_value.day} de {months[local_value.month - 1]} de {local_value.year}"


def _shell_context(section: str) -> dict:
    profile = getattr(current_user, "staff_profile", None)
    permissions = permissions_for_user(current_user)
    visible_sections = {"operations"}
    section_permissions = {
        "orders": "orders.view_related",
        "fulfillment": "fulfillment.view_assigned",
        "scanner": "scanner.use",
        "inventory": "inventory.view_assigned_point",
        "products": "products.moderate",
        "stores": "stores.moderate",
        "reviews": "reviews.view",
        "payments": "payments.view",
        "payouts": "payouts.view",
    }
    visible_sections.update(
        key for key, permission in section_permissions.items()
        if permission in permissions
    )
    if "admin.users.view" in permissions or "admin.staff.view" in permissions:
        visible_sections.add("users")
    if profile is None or profile.role == StaffRole.SUPER_ADMIN:
        visible_sections.update(
            item.key
            for group in ADMIN_NAVIGATION
            for item in group.items
            if not item.implemented
        )
        visible_sections.update(item.key for item in ADMIN_SECONDARY_NAVIGATION)

    return {
        "admin_navigation": ADMIN_NAVIGATION,
        "admin_secondary_navigation": ADMIN_SECONDARY_NAVIGATION,
        "current_admin_section": section,
        "admin_permissions": permissions,
        "admin_visible_sections": visible_sections,
        "admin_session_profile": profile,
        "admin_session_role_label": (
            STAFF_ROLE_LABELS.get(profile.role) if profile is not None else None
        ),
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


_ADMIN_PAYMENT_QUERY_KEYS = (
    "tab",
    "q",
    "method",
    "status",
    "date_from",
    "date_to",
    "amount_min",
    "amount_max",
    "analysis",
    "page",
    "per_page",
    "sort_by",
    "sort_direction",
    "detail",
)
_ADMIN_PAYMENT_TABS = (
    ("all", "Todos"),
    ("manual_review", "Revisión manual"),
    ("approved", "Aprobados"),
    ("rejected_failed", "Rechazados / fallidos"),
    ("expired", "Expirados"),
)
_ADMIN_PAYMENT_METHOD_LABELS = {
    PaymentMethod.BANK_TRANSFER.value: "Transferencia bancaria",
    PaymentMethod.CARD.value: "Tarjeta",
}
_ADMIN_PAYMENT_ANALYSIS_LABELS = {
    PaymentProofPrecheckOutcome.PASSED.value: "Correcta",
    PaymentProofPrecheckOutcome.NEEDS_MANUAL_REVIEW.value: "Revisión manual requerida",
    PaymentProofPrecheckOutcome.FAILED.value: "Fallida",
    "NO_ANALYSIS": "Sin prevalidación",
}


def _admin_payment_query_state() -> dict[str, str]:
    return {
        key: value
        for key in _ADMIN_PAYMENT_QUERY_KEYS
        if (value := (request.args.get(key) or "").strip())
    }


def _admin_payment_return_state(payment_code: str) -> dict[str, str]:
    """Build a closed, local return context for payment mutations."""
    values = {
        key: value
        for key in _ADMIN_PAYMENT_QUERY_KEYS
        if key != "detail"
        and (value := (request.form.get(key) or "").strip())
    }
    values["detail"] = payment_code
    return values


def _admin_payment_url_builder():
    base = _admin_payment_query_state()

    def build(**overrides) -> str:
        values = dict(base)
        for key, value in overrides.items():
            if key not in _ADMIN_PAYMENT_QUERY_KEYS:
                continue
            if value is None or value == "":
                values.pop(key, None)
            else:
                values[key] = str(value)
        return url_for("admin.payments", **values)

    return build


def _admin_payment_pagination_window(page: int, pages: int) -> tuple[int | None, ...]:
    if pages <= 0:
        return ()
    if pages <= 7:
        return tuple(range(1, pages + 1))
    candidates = {1, pages, page - 1, page, page + 1}
    values = sorted(value for value in candidates if 1 <= value <= pages)
    window: list[int | None] = []
    previous = 0
    for value in values:
        if previous and value - previous > 1:
            window.append(None)
        window.append(value)
        previous = value
    return tuple(window)


@admin.get("/payments")
@admin_permission_required("payments.view")
def payments():
    active_tab = (request.args.get("tab") or "all").strip().lower()
    query_state = _admin_payment_query_state()
    try:
        payment_list = list_admin_payments(
            db.session,
            current_user=current_user,
            tab=active_tab,
            query=request.args.get("q"),
            method=request.args.get("method"),
            status=request.args.get("status"),
            date_from=request.args.get("date_from"),
            date_to=request.args.get("date_to"),
            amount_min=request.args.get("amount_min"),
            amount_max=request.args.get("amount_max"),
            analysis=request.args.get("analysis"),
            page=request.args.get("page", 1),
            per_page=request.args.get("per_page", 20),
            sort_by=request.args.get("sort_by", "created_at"),
            sort_direction=request.args.get("sort_direction", "desc"),
        )
    except AdminPaymentQueryError as exc:
        flash(str(exc), "warning")
        safe_tabs = {key for key, _label in _ADMIN_PAYMENT_TABS}
        return redirect(url_for(
            "admin.payments",
            tab=active_tab if active_tab in safe_tabs else "all",
        ))

    detail = None
    detail_code = query_state.get("detail")
    if detail_code:
        try:
            detail = get_admin_payment_detail(
                db.session,
                detail_code,
                current_user=current_user,
            )
        except AdminPaymentNotFoundError:
            abort(404)

    kpis = get_admin_payment_kpis(db.session)
    payment_url = _admin_payment_url_builder()
    filter_keys = {
        "q", "method", "status", "date_from", "date_to",
        "amount_min", "amount_max", "analysis",
    }
    return render_template(
        "admin/payments.html",
        kpis=kpis,
        payments=payment_list,
        detail=detail,
        active_tab=active_tab,
        payment_tabs=_ADMIN_PAYMENT_TABS,
        payment_method_labels=_ADMIN_PAYMENT_METHOD_LABELS,
        payment_statuses=tuple(
            (status.value, {
                PaymentStatus.AWAITING_PROOF: "Esperando comprobante",
                PaymentStatus.PENDING_PROVIDER: "Esperando proveedor",
                PaymentStatus.PROCESSING: "En revisión",
                PaymentStatus.APPROVED: "Aprobado",
                PaymentStatus.REJECTED: "Rechazado",
                PaymentStatus.FAILED: "Fallido",
                PaymentStatus.CANCELLED: "Cancelado",
                PaymentStatus.EXPIRED: "Expirado",
            }[status])
            for status in PaymentStatus
        ),
        payment_analysis_labels=_ADMIN_PAYMENT_ANALYSIS_LABELS,
        payment_rejection_public_reasons=PAYMENT_REJECTION_PUBLIC_REASONS,
        payment_rejection_reason_labels=PAYMENT_REJECTION_REASON_LABELS,
        payment_return_query_keys=tuple(
            key for key in _ADMIN_PAYMENT_QUERY_KEYS if key != "detail"
        ),
        payment_query=query_state,
        payment_url=payment_url,
        pagination_window=_admin_payment_pagination_window(
            payment_list.page, payment_list.pages
        ),
        has_payment_filters=any(query_state.get(key) for key in filter_keys),
        has_advanced_payment_filters=any(
            query_state.get(key)
            for key in ("date_from", "date_to", "amount_min", "amount_max", "analysis")
        ),
        **_shell_context("payments"),
    )


@admin.get("/payments/<string:payment_code>/proof")
@admin_permission_required("payments.view")
def payment_attempt_proof(payment_code: str):
    normalized = " ".join((payment_code or "").split()).upper()
    attempt = db.session.scalar(
        select(PaymentAttempt).where(PaymentAttempt.public_code == normalized)
    )
    if attempt is None:
        abort(404)
    proof = db.session.scalar(
        select(PaymentProof).where(PaymentProof.payment_attempt_id == attempt.id)
    )
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
    download_name = PurePath(proof.original_filename or "comprobante").name
    response = send_file(
        path,
        mimetype=proof.media_type,
        as_attachment=request.args.get("download") == "1",
        download_name=download_name or "comprobante",
        conditional=False,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _payment_attempt_and_proof_or_404(
    payment_code: str,
) -> tuple[PaymentAttempt, PaymentProof | None]:
    normalized = " ".join((payment_code or "").split()).upper()
    attempt = db.session.scalar(
        select(PaymentAttempt).where(PaymentAttempt.public_code == normalized)
    )
    if attempt is None:
        abort(404)
    proof = db.session.scalar(
        select(PaymentProof).where(PaymentProof.payment_attempt_id == attempt.id)
    )
    return attempt, proof


def _payment_decision_redirect(payment_code: str):
    return redirect(
        url_for(
            "admin.payments",
            **_admin_payment_return_state(payment_code),
        )
    )


def _review_payment_attempt(payment_code: str, decision: str):
    attempt, proof = _payment_attempt_and_proof_or_404(payment_code)
    if attempt.method != PaymentMethod.BANK_TRANSFER or proof is None:
        flash("Este intento de pago no admite una decisión manual.", "error")
        return _payment_decision_redirect(attempt.public_code)

    reason_code = None
    reason = None
    notes = (request.form.get("notes") or "").strip() or None
    if notes and len(notes) > 1000:
        flash("Las notas no pueden superar 1000 caracteres.", "error")
        return _payment_decision_redirect(attempt.public_code)

    if decision == "reject":
        reason_code = (request.form.get("reason_code") or "").strip().upper()
        if reason_code not in PAYMENT_REJECTION_REASON_LABELS:
            flash("Selecciona un motivo de rechazo válido.", "error")
            return _payment_decision_redirect(attempt.public_code)
        if reason_code == "OTHER":
            reason = " ".join(
                (request.form.get("custom_reason") or "").split()
            )
            if not reason:
                flash("Describe el motivo del rechazo.", "error")
                return _payment_decision_redirect(attempt.public_code)
            if len(reason) > 500:
                flash("El motivo no puede superar 500 caracteres.", "error")
                return _payment_decision_redirect(attempt.public_code)
        else:
            reason = PAYMENT_REJECTION_PUBLIC_REASONS[reason_code]

    try:
        result = review_payment_proof(
            session=db.session,
            proof_id=proof.id,
            decision=decision,
            reviewer_user_id=current_user.id,
            storage_root=current_app.config["PAYMENT_PROOF_UPLOAD_DIR"],
            reason_code=reason_code,
            reason=reason,
            notes=notes,
        )
        db.session.commit()
    except PaymentProofExpiredError:
        db.session.rollback()
        flash(
            "No es posible aprobar este pago porque la reserva del pedido ya venció.",
            "error",
        )
    except InvalidPaymentProofTransitionError:
        db.session.rollback()
        flash(
            "El pago ya fue decidido por otro operador. Consulta su estado actualizado.",
            "warning",
        )
    except PaymentProofIntegrityError:
        db.session.rollback()
        flash(
            "No se pudo verificar la integridad del comprobante. "
            "No se aplicó ninguna decisión.",
            "error",
        )
    except PaymentProofServiceError:
        db.session.rollback()
        flash("No se pudo aplicar la decisión solicitada.", "error")
    except IntegrityError:
        db.session.rollback()
        current_app.logger.warning(
            "Conflicto concurrente al revisar payment_code=%s",
            attempt.public_code,
        )
        flash(
            "El pago cambió mientras lo revisabas. Consulta su estado actualizado.",
            "warning",
        )
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Falló la decisión Admin payment_code=%s", attempt.public_code
        )
        flash("No pudimos guardar la decisión. Inténtalo nuevamente.", "error")
    else:
        if result.replayed:
            flash(
                "Este pago ya había sido aprobado."
                if result.proof_status == PaymentProofStatus.APPROVED
                else "Este pago ya había sido rechazado.",
                "info",
            )
        else:
            flash(
                f"Pago {attempt.public_code} aprobado correctamente."
                if result.proof_status == PaymentProofStatus.APPROVED
                else f"Pago {attempt.public_code} rechazado.",
                "success",
            )
    return _payment_decision_redirect(attempt.public_code)


@admin.post("/payments/<string:payment_code>/approve")
@limiter.limit("20 per hour")
@admin_permission_required("payments.review")
def approve_payment_attempt(payment_code: str):
    return _review_payment_attempt(payment_code, "approve")


@admin.post("/payments/<string:payment_code>/reject")
@limiter.limit("20 per hour")
@admin_permission_required("payments.review")
def reject_payment_attempt(payment_code: str):
    return _review_payment_attempt(payment_code, "reject")


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
@admin_permission_required("payments.view")
def payment_review(order_number: str):
    review = _payment_review_or_404(order_number)
    return render_template(
        "admin/payment_review.html",
        review=review,
        payment_rejection_reason_labels=PAYMENT_REJECTION_REASON_LABELS,
        **_shell_context("orders"),
    )


@admin.get("/orders/<string:order_number>/payment-proof")
@admin_permission_required("payments.view")
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
    reason_code = request.form.get("reason_code")
    notes = request.form.get("notes")
    if decision == "reject" and not (reason or "").strip():
        flash("El rechazo requiere una razón.", "error")
        return redirect(url_for("admin.payment_review", order_number=order_number))
    try:
        result = review_payment_proof(
            session=db.session,
            proof_id=review.payment.proof_id,
            decision=decision,
            reviewer_user_id=current_user.id,
            storage_root=current_app.config["PAYMENT_PROOF_UPLOAD_DIR"],
            reason_code=reason_code,
            reason=reason,
            notes=notes,
        )
        db.session.commit()
    except PaymentProofExpiredError:
        db.session.rollback()
        flash("La reserva del pedido ya venció; no se aplicó ninguna decisión.", "error")
    except InvalidPaymentProofTransitionError:
        db.session.rollback()
        flash("Este comprobante ya fue decidido por otro operador.", "error")
    except PaymentProofIntegrityError:
        db.session.rollback()
        flash(
            "No se pudo verificar la integridad del comprobante. "
            "No se aplicó ninguna decisión.",
            "error",
        )
    except PaymentProofServiceError:
        db.session.rollback()
        flash("No se pudo aplicar la decisión solicitada.", "error")
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
@admin_permission_required("payments.review")
def approve_payment(order_number: str):
    return _review_payment(order_number, "approve")


@admin.post("/orders/<string:order_number>/payment/reject")
@limiter.limit("20 per hour")
@admin_permission_required("payments.review")
def reject_payment(order_number: str):
    return _review_payment(order_number, "reject")


def _draft_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


@admin.get("/stores")
@ecuvel_staff_required
def stores():
    page = get_admin_stores_page(
        db.session,
        tab=request.args.get("status", "pending"),
        query=request.args.get("q", ""),
        province=request.args.get("province", ""),
        city=request.args.get("city", ""),
        document_state=request.args.get("documents", ""),
        date_from=request.args.get("date_from", ""),
        date_to=request.args.get("date_to", ""),
        page=request.args.get("page", 1, type=int) or 1,
    )
    return render_template(
        "admin/stores.html",
        page=page,
        **_shell_context("stores"),
    )


@admin.get("/stores/<uuid:onboarding_id>")
@ecuvel_staff_required
def store_review(onboarding_id: uuid.UUID):
    onboarding = get_admin_store_review(db.session, onboarding_id)
    if onboarding is None:
        abort(404)
    bank_version = onboarding_bank_account_version(db.session, onboarding)
    can_reveal_bank_account = user_has_permission(
        current_user,
        "bank_accounts.sensitive.view",
    )
    return render_template(
        "admin/store_review.html",
        onboarding=onboarding,
        bank_summary=bank_account_summary(bank_version),
        can_reveal_bank_account=can_reveal_bank_account,
        approval_checks=APPROVAL_CHECK_LABELS,
        correction_reasons=CORRECTION_REASON_LABELS,
        document_correction_reasons=DOCUMENT_CORRECTION_REASON_LABELS,
        field_correction_reasons=FIELD_CORRECTION_REASON_LABELS,
        documents_by_id={str(document.id): document for document in onboarding.documents},
        document_type_label=document_type_label,
        document_status_label=document_status_label,
        contract_status_label=contract_status_label,
        **_shell_context("stores"),
    )


@admin.post("/stores/<uuid:onboarding_id>/bank-account/reveal")
@limiter.limit("10 per hour")
@admin_permission_required("bank_accounts.sensitive.view")
def reveal_store_bank_account(onboarding_id: uuid.UUID):
    onboarding = get_admin_store_review(db.session, onboarding_id)
    if onboarding is None:
        abort(404)
    version = onboarding_bank_account_version(db.session, onboarding, lock=True)
    try:
        requested_version_id = uuid.UUID(request.form.get("bank_account_version_id", ""))
    except (TypeError, ValueError):
        abort(404)
    if version is None or version.id != requested_version_id:
        abort(404)
    try:
        account_number = decrypt_bank_account_for_staff(
            version,
            staff_user=current_user,
        )
    except BankAccountAccessError:
        db.session.rollback()
        abort(404)
    record_financial_audit(
        db.session,
        action=BANK_ACCOUNT_SENSITIVE_VIEWED,
        actor_user_id=current_user.id,
        metadata={
            "store_id": str(version.store_id),
            "bank_account_version_id": str(version.id),
            "status": version.status.value,
        },
    )
    db.session.commit()
    response = jsonify({"account_number": account_number})
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@admin.get("/stores/<uuid:onboarding_id>/documents/<uuid:document_id>")
@ecuvel_staff_required
def store_document(onboarding_id: uuid.UUID, document_id: uuid.UUID):
    onboarding = get_admin_store_review(db.session, onboarding_id)
    if onboarding is None:
        abort(404)
    document = next((item for item in onboarding.documents if item.id == document_id), None)
    if document is None or document.mime_type not in {
        "application/pdf", "image/jpeg", "image/png"
    }:
        abort(404)
    try:
        path = verify_private_file(
            root=current_app.config["PARTNER_DOCUMENT_UPLOAD_DIR"],
            storage_key=document.storage_key,
            size_bytes=document.size_bytes,
            sha256=document.sha256,
        )
    except PrivateStorageError:
        abort(404)
    response = send_file(
        path,
        mimetype=document.mime_type,
        as_attachment=request.args.get("download") == "1",
        download_name=document.file_name,
        conditional=False,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@admin.get("/stores/<uuid:onboarding_id>/contract")
@ecuvel_staff_required
def store_contract(onboarding_id: uuid.UUID):
    onboarding = get_admin_store_review(db.session, onboarding_id)
    acceptance = onboarding.contract_acceptance if onboarding is not None else None
    if acceptance is None or not acceptance.pdf_storage_key:
        abort(404)
    try:
        path = private_file_path(
            current_app.config["PARTNER_CONTRACT_UPLOAD_DIR"],
            acceptance.pdf_storage_key,
        )
    except PrivateStorageError:
        abort(404)
    if not path.is_file():
        abort(404)
    response = send_file(
        path,
        mimetype="application/pdf",
        as_attachment=request.args.get("download") == "1",
        download_name="contrato-aceptado-ecuvel-partners.pdf",
        conditional=False,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _store_review_decision(onboarding_id: uuid.UUID, action: str):
    try:
        db.session.remove()
        with db.session.begin():
            if action == "approve":
                approve_store_verification(
                    db.session,
                    onboarding_id=onboarding_id,
                    reviewer_user_id=current_user.id,
                    checklist_values=request.form.getlist("checklist"),
                    expected_updated_at=request.form.get("expected_updated_at"),
                    comments=request.form.get("comments"),
                )
            else:
                request_store_corrections(
                    db.session,
                    onboarding_id=onboarding_id,
                    reviewer_user_id=current_user.id,
                    form=request.form,
                )
    except PartnerOnboardingStateError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    except PartnerOnboardingValidationError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except PartnerOnboardingError:
        db.session.rollback()
        flash("No pudimos guardar la revisión.", "error")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falló la revisión de onboarding %s", onboarding_id)
        flash("No pudimos guardar la revisión. Inténtalo nuevamente.", "error")
    else:
        flash(
            "Verificación aprobada. El seller debe aceptar el contrato para activar la tienda."
            if action == "approve"
            else "Correcciones enviadas al seller.",
            "success",
        )
        return redirect(url_for("admin.stores", status="contract" if action == "approve" else "corrections"))
    return redirect(url_for("admin.store_review", onboarding_id=onboarding_id))


@admin.post("/stores/<uuid:onboarding_id>/approve")
@limiter.limit("30 per hour")
@admin_permission_required("bank_accounts.sensitive.view")
def approve_store(onboarding_id: uuid.UUID):
    return _store_review_decision(onboarding_id, "approve")


@admin.post("/stores/<uuid:onboarding_id>/request-corrections")
@limiter.limit("30 per hour")
@ecuvel_staff_required
def request_store_changes(onboarding_id: uuid.UUID):
    return _store_review_decision(onboarding_id, "corrections")


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
    commission_rows = draft_commission_display_rows(db.session, draft)
    return render_template(
        "admin/product_preview.html",
        draft=draft,
        draft_view=draft_view,
        preview=preview,
        commission_rows=commission_rows,
        commission_snapshot_complete=commission_snapshot_complete(
            draft, commission_rows
        ),
        publication_error=None,
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


def _render_product_publication_error(draft_id: uuid.UUID, message: str):
    """Render a failed publication beside the controls that triggered it."""

    if request.form.get("source") == "preview":
        draft = get_admin_product_draft(db.session, draft_id)
        if draft is None:
            flash(message, "error")
            return _moderation_redirect(draft_id)
        draft_view = build_product_draft_view(draft)
        preview = build_product_draft_preview(
            draft_view,
            requested_sku=request.args.get("variant"),
            selected_view="storefront",
            media_endpoint="admin.product_file",
        )
        commission_rows = draft_commission_display_rows(db.session, draft)
        return render_template(
            "admin/product_preview.html",
            draft=draft,
            draft_view=draft_view,
            preview=preview,
            commission_rows=commission_rows,
            commission_snapshot_complete=commission_snapshot_complete(
                draft, commission_rows
            ),
            publication_error=message,
            **_shell_context("products"),
        ), 422

    page = get_admin_products_page(
        db.session,
        status_key="review",
        query="",
        page=1,
        selected_id=draft_id,
    )
    return render_template(
        "admin/products.html",
        page=page,
        publication_error=message,
        **_shell_context("products"),
    ), 422


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
            image_processing_config=product_image_processing_config(
                current_app.config
            ),
        )
        copied_files = result.copied_files
        db.session.commit()
    except ProductModerationError as exc:
        db.session.rollback()
        remove_copied_publication_files(copied_files)
        return _render_product_publication_error(draft_id, str(exc))
    except IntegrityError:
        db.session.rollback()
        remove_copied_publication_files(copied_files)
        current_app.logger.info(
            "Conflicto de identificador al publicar el producto %s", draft_id
        )
        return _render_product_publication_error(
            draft_id,
            "No se pudo publicar porque un SKU, código de barras u otro "
            "identificador ya existe en el catálogo.",
        )
    except Exception:
        db.session.rollback()
        remove_copied_publication_files(copied_files)
        current_app.logger.exception("Falló la publicación del producto %s", draft_id)
        return _render_product_publication_error(
            draft_id,
            "No pudimos publicar el producto. No se guardó ningún cambio parcial.",
        )
    else:
        flash(
            "El producto ya estaba publicado."
            if result.already_published else "Producto aprobado y publicado correctamente.",
            "success",
        )
        return redirect(url_for("admin.products", status="approved", draft=draft_id))
    return _moderation_redirect(draft_id)


def _admin_users_return(endpoint: str, **extra):
    values = {
        "q": request.form.get("return_q", "").strip(),
        "filter": request.form.get("return_filter", "").strip(),
        "page": request.form.get("return_page", type=int),
        **extra,
    }
    return redirect(url_for(endpoint, **{key: value for key, value in values.items() if value}))


def _admin_transaction_error(exc: Exception, fallback: str) -> None:
    db.session.rollback()
    if isinstance(exc, AdminUserError):
        flash(str(exc), "error")
        return
    current_app.logger.exception(fallback)
    flash("No pudimos completar la operación. Inténtalo nuevamente.", "error")


@admin.get("/users")
@admin_permission_required("admin.users.view")
def users():
    page = get_admin_clients_page(
        db.session,
        query=request.args.get("q", ""),
        filter_key=request.args.get("filter", "all"),
        page=request.args.get("page", 1, type=int) or 1,
    )
    return render_template(
        "admin/users.html", page=page,
        can_manage=user_has_permission(current_user, "admin.users.manage"),
        **_shell_context("users"),
    )


@admin.get("/users/staff")
@admin_permission_required("admin.staff.view")
def staff():
    page = get_admin_staff_page(
        db.session,
        query=request.args.get("q", ""),
        role=request.args.get("role", ""),
        status=request.args.get("status", ""),
        operational=request.args.get("operational", ""),
        warehouse_id=request.args.get("warehouse_id", ""),
        page=request.args.get("page", 1, type=int) or 1,
    )
    return render_template(
        "admin/staff.html", page=page, role_labels=STAFF_ROLE_LABELS,
        employment_labels=EMPLOYMENT_LABELS, operational_labels=OPERATIONAL_LABELS,
        warehouses=operational_warehouses(db.session),
        **_shell_context("users"),
    )


@admin.route("/users/staff/new", methods=["GET", "POST"])
@limiter.limit("20 per hour")
@admin_permission_required("admin.staff.manage")
def staff_new():
    warehouses = operational_warehouses(db.session)
    if request.method == "GET":
        return render_template(
            "admin/staff_form.html", profile=None, warehouses=warehouses,
            role_labels=STAFF_ROLE_LABELS, employment_labels=EMPLOYMENT_LABELS,
            identification_types=tuple(StaffIdentificationType), permissions=PERMISSION_LABELS,
            role_permissions={role.value: tuple(sorted(values)) for role, values in ROLE_PERMISSIONS.items()},
            form={}, **_shell_context("users"),
        )
    form = request.form.to_dict()
    warehouse_id = None
    if request.form.get("warehouse_id"):
        try:
            warehouse_id = uuid.UUID(request.form["warehouse_id"])
        except ValueError:
            flash("El Punto ECUVEL seleccionado no es válido.", "error")
            return render_template(
                "admin/staff_form.html", profile=None, warehouses=warehouses,
                role_labels=STAFF_ROLE_LABELS, employment_labels=EMPLOYMENT_LABELS,
                identification_types=tuple(StaffIdentificationType), permissions=PERMISSION_LABELS,
                role_permissions={role.value: tuple(sorted(values)) for role, values in ROLE_PERMISSIONS.items()},
                form=form, **_shell_context("users"),
            ), 400
    started_at = None
    if request.form.get("employment_started_at"):
        try:
            started_at = datetime.strptime(request.form["employment_started_at"], "%Y-%m-%d").date()
        except ValueError:
            flash("La fecha de ingreso no es válida.", "error")
            return render_template(
                "admin/staff_form.html", profile=None, warehouses=warehouses,
                role_labels=STAFF_ROLE_LABELS, employment_labels=EMPLOYMENT_LABELS,
                identification_types=tuple(StaffIdentificationType), permissions=PERMISSION_LABELS,
                role_permissions={role.value: tuple(sorted(values)) for role, values in ROLE_PERMISSIONS.items()},
                form=form, **_shell_context("users"),
            ), 400
    actor_id = current_user.id
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            created = create_staff_member(
                database_session, actor_user_id=actor_id,
                first_names=request.form.get("first_names", ""),
                last_names=request.form.get("last_names", ""),
                email=request.form.get("email", ""), phone=request.form.get("phone", ""),
                identification_type=request.form.get("identification_type", ""),
                identification_number=request.form.get("identification_number", ""),
                nationality_code=request.form.get("nationality_code", "ECU"),
                role=request.form.get("role", ""),
                employment_status=request.form.get("employment_status", "PENDING"),
                employment_started_at=started_at, warehouse_id=warehouse_id,
                invitation_ttl_minutes=current_app.config["STAFF_INVITATION_TTL_MINUTES"],
                link_existing_user=request.form.get("link_existing_user") == "1",
            )
        employee_code = created.profile.employee_code
        invitation_sent = False
        if created.invitation_token:
            link = build_mail_action_url(
                "auth.staff_invitation", token=created.invitation_token
            )
            try:
                mail_service.send(staff_invitation_mail(
                    to=created.profile.user.email,
                    action_url=link,
                    full_name=created.profile.user.full_name,
                    employee_code=created.profile.employee_code,
                    role=STAFF_ROLE_LABELS.get(
                        created.profile.role, created.profile.role
                    ),
                    expiration_minutes=current_app.config[
                        "STAFF_INVITATION_TTL_MINUTES"
                    ],
                ))
                invitation_sent = True
            except MailError as exc:
                current_app.logger.warning(
                    "event=mail_failed mail_type=STAFF_INVITATION error=%s",
                    type(exc).__name__,
                )
        message = f"Personal {employee_code} registrado."
        if invitation_sent:
            message += " La invitación se envió por email."
        elif created.invitation_token:
            message += (
                " No pudimos enviar la invitación; puedes reenviarla desde "
                "el perfil del empleado."
            )
        else:
            message += " Se vinculó la cuenta existente sin cambiar sus credenciales."
        category = (
            "success"
            if invitation_sent or not created.invitation_token
            else "warning"
        )
        flash(message, category)
        return redirect(url_for("admin.staff_detail", employee_code=employee_code))
    except Exception as exc:
        _admin_transaction_error(exc, "Falló el registro de personal ECUVEL")
        return render_template(
            "admin/staff_form.html", profile=None, warehouses=warehouses,
            role_labels=STAFF_ROLE_LABELS, employment_labels=EMPLOYMENT_LABELS,
            identification_types=tuple(StaffIdentificationType), permissions=PERMISSION_LABELS,
            role_permissions={role.value: tuple(sorted(values)) for role, values in ROLE_PERMISSIONS.items()},
            form=form, **_shell_context("users"),
        ), 400


@admin.get("/users/staff/<string:employee_code>")
@admin_permission_required("admin.staff.view")
def staff_detail(employee_code: str):
    detail = get_admin_staff_detail(db.session, employee_code)
    if detail is None:
        abort(404)
    return render_template(
        "admin/staff_detail.html", detail=detail,
        role_labels=STAFF_ROLE_LABELS, employment_labels=EMPLOYMENT_LABELS,
        operational_labels=OPERATIONAL_LABELS,
        can_manage=user_has_permission(current_user, "admin.staff.manage"),
        **_shell_context("users"),
    )


@admin.route("/users/staff/<string:employee_code>/edit", methods=["GET", "POST"])
@limiter.limit("40 per hour")
@admin_permission_required("admin.staff.manage")
def staff_edit(employee_code: str):
    profile = find_staff_by_employee_code(db.session, employee_code)
    if profile is None:
        abort(404)
    warehouses = operational_warehouses(db.session)
    if request.method == "GET":
        return render_template(
            "admin/staff_edit.html", profile=profile, warehouses=warehouses,
            role_labels=STAFF_ROLE_LABELS, employment_labels=EMPLOYMENT_LABELS,
            permissions=PERMISSION_LABELS,
            role_permissions={role.value: tuple(sorted(values)) for role, values in ROLE_PERMISSIONS.items()},
            **_shell_context("users"),
        )
    warehouse_id = None
    if request.form.get("warehouse_id"):
        try:
            warehouse_id = uuid.UUID(request.form["warehouse_id"])
        except ValueError:
            flash("El Punto ECUVEL seleccionado no es válido.", "error")
            return redirect(url_for("admin.staff_edit", employee_code=employee_code))
    actor_id = current_user.id
    try:
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            locked = find_staff_by_employee_code(
                database_session,
                employee_code,
                for_update=True,
            )
            if locked is None:
                abort(404)
            update_staff_profile(
                database_session, profile=locked, actor_user_id=actor_id,
                role=request.form.get("role", ""),
                employment_status=request.form.get("employment_status", ""),
                phone=request.form.get("phone", ""), warehouse_id=warehouse_id,
                reason=request.form.get("reason", ""),
            )
        flash("Perfil laboral actualizado.", "success")
    except Exception as exc:
        _admin_transaction_error(exc, "Falló la actualización de personal ECUVEL")
    return redirect(url_for("admin.staff_detail", employee_code=employee_code))


@admin.post("/users/staff/<string:employee_code>/access")
@limiter.limit("30 per hour")
@admin_permission_required("admin.staff.manage")
def staff_access(employee_code: str):
    actor_id = current_user.id
    try:
        db.session.remove(); database_session = db.session()
        with database_session.begin():
            profile = find_staff_by_employee_code(
                database_session,
                employee_code,
                for_update=True,
            )
            if profile is None:
                abort(404)
            set_staff_access(
                database_session, profile=profile, actor_user_id=actor_id,
                enable=request.form.get("action") == "enable",
                reason=request.form.get("reason", ""),
            )
        flash("Estado de acceso actualizado.", "success")
    except Exception as exc:
        _admin_transaction_error(exc, "Falló el cambio de acceso del personal")
    return redirect(url_for("admin.staff_detail", employee_code=employee_code))


@admin.post("/users/staff/<string:employee_code>/invite")
@limiter.limit("10 per hour")
@admin_permission_required("admin.staff.manage")
def staff_invite(employee_code: str):
    actor_id = current_user.id
    try:
        db.session.remove(); database_session = db.session()
        with database_session.begin():
            profile = find_staff_by_employee_code(database_session, employee_code)
            if profile is None:
                abort(404)
            token = create_staff_invitation(
                database_session, profile=profile, actor_user_id=actor_id,
                ttl_minutes=current_app.config["STAFF_INVITATION_TTL_MINUTES"],
            )
            destination = profile.user.email
        link = build_mail_action_url("auth.staff_invitation", token=token)
        try:
            mail_service.send(staff_invitation_mail(
                to=destination,
                action_url=link,
                full_name=profile.user.full_name,
                employee_code=profile.employee_code,
                role=STAFF_ROLE_LABELS.get(profile.role, profile.role),
                expiration_minutes=current_app.config[
                    "STAFF_INVITATION_TTL_MINUTES"
                ],
            ))
        except MailError as exc:
            current_app.logger.warning(
                "event=mail_failed mail_type=STAFF_INVITATION error=%s",
                type(exc).__name__,
            )
            flash(
                "No pudimos enviar la invitación. Puedes intentarlo nuevamente.",
                "warning",
            )
        else:
            flash("Invitación enviada. La anterior quedó revocada.", "success")
    except Exception as exc:
        _admin_transaction_error(exc, "Falló el envío de invitación de personal")
    return redirect(url_for("admin.staff_detail", employee_code=employee_code))


@admin.post("/users/staff/<string:employee_code>/invite/revoke")
@limiter.limit("20 per hour")
@admin_permission_required("admin.staff.manage")
def staff_invite_revoke(employee_code: str):
    actor_id = current_user.id
    try:
        db.session.remove(); database_session = db.session()
        with database_session.begin():
            profile = find_staff_by_employee_code(database_session, employee_code)
            if profile is None:
                abort(404)
            revoked = revoke_staff_invitations(
                database_session,
                profile=profile,
                actor_user_id=actor_id,
                reason=request.form.get("reason", ""),
            )
        flash(f"{revoked} invitación(es) pendiente(s) revocada(s).", "success")
    except Exception as exc:
        _admin_transaction_error(exc, "Falló la revocación de invitaciones de personal")
    return redirect(url_for("admin.staff_detail", employee_code=employee_code))


@admin.get("/users/<string:identifier>")
@admin_permission_required("admin.users.view")
def user_detail(identifier: str):
    detail = get_admin_client_detail(db.session, identifier)
    if detail is None:
        abort(404)
    return render_template(
        "admin/user_detail.html", detail=detail,
        can_manage=user_has_permission(current_user, "admin.users.manage"),
        **_shell_context("users"),
    )


@admin.post("/users/<string:identifier>/password-reset")
@limiter.limit("10 per hour")
@admin_permission_required("admin.users.manage")
def user_password_reset(identifier: str):
    actor_id = current_user.id
    destination = None; token = None
    try:
        db.session.remove(); database_session = db.session()
        with database_session.begin():
            user = find_user_by_public_code(database_session, identifier)
            if user is None:
                abort(404)
            result = request_password_reset(
                session=database_session, email=user.email or "",
                ttl_minutes=current_app.config["PASSWORD_RESET_TOKEN_TTL_MINUTES"],
            )
            record_admin_audit(
                database_session, actor_user_id=actor_id, target_user_id=user.id,
                action="ADMIN_PASSWORD_RESET_REQUESTED",
            )
            if result:
                destination, token = result[0].email, result[1]
        if destination and token:
            link = build_mail_action_url("auth.reset_password_form", token=token)
            mail_service.send(password_reset_mail(
                to=destination,
                action_url=link,
                expiration_minutes=current_app.config[
                    "PASSWORD_RESET_TOKEN_TTL_MINUTES"
                ],
            ))
        flash("Si la cuenta permite recuperación, el enlace fue enviado.", "success")
    except Exception as exc:
        _admin_transaction_error(exc, "Falló el reset administrativo de contraseña")
    return redirect(url_for("admin.user_detail", identifier=identifier))


@admin.post("/users/<string:identifier>/status")
@limiter.limit("30 per hour")
@admin_permission_required("admin.users.manage")
def user_status(identifier: str):
    actor_id = current_user.id
    try:
        db.session.remove(); database_session = db.session()
        with database_session.begin():
            user = find_user_by_public_code(
                database_session,
                identifier,
                for_update=True,
            )
            if user is None:
                abort(404)
            set_user_suspension(
                database_session, user=user, actor_user_id=actor_id,
                suspend=request.form.get("action") == "suspend",
                reason=request.form.get("reason", ""),
            )
        flash("Estado de la cuenta actualizado.", "success")
    except Exception as exc:
        _admin_transaction_error(exc, "Falló el cambio de estado del usuario")
    return redirect(url_for("admin.user_detail", identifier=identifier))


@admin.get("/reviews")
@admin_permission_required("reviews.view")
def reviews():
    page = get_admin_reviews_page(
        db.session,
        tab=request.args.get("tab", "manual"),
        q=request.args.get("q", ""),
        rating=request.args.get("rating", ""),
        risk=request.args.get("risk", ""),
        category=request.args.get("category", ""),
        page=request.args.get("page", 1),
    )
    detail = None
    if request.args.get("detail"):
        try:
            detail_id = uuid.UUID(request.args["detail"])
        except (TypeError, ValueError):
            abort(404)
        detail = get_admin_review_detail(db.session, detail_id)
        if detail is None:
            abort(404)
    return render_template(
        "admin/reviews.html",
        page=page,
        detail=detail,
        rejection_reasons=REJECTION_REASON_LABELS,
        can_moderate=user_has_permission(current_user, "reviews.moderate"),
        **_shell_context("reviews"),
    )


@admin.get("/reviews/images/<string:public_id>")
@admin_permission_required("reviews.view")
def review_image(public_id: str):
    image = db.session.scalar(
        select(ProductReviewImage).where(ProductReviewImage.public_id == public_id)
    )
    if image is None or image.revision_id != image.review.current_revision_id:
        abort(404)
    try:
        path = private_file_path(
            current_app.config["PRODUCT_REVIEW_UPLOAD_DIR"], image.storage_key
        )
    except PrivateStorageError:
        abort(404)
    if not path.is_file():
        abort(404)
    response = send_file(path, mimetype=image.media_type, as_attachment=False, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@admin.post("/reviews/<uuid:review_id>/decision")
@limiter.limit("30 per hour")
@admin_permission_required("reviews.moderate")
def review_decision(review_id: uuid.UUID):
    return_params = {
        "tab": request.form.get("return_tab", "manual").strip(),
        "q": request.form.get("return_q", "").strip(),
        "rating": request.form.get("return_rating", type=int),
        "risk": request.form.get("return_risk", "").strip(),
        "category": request.form.get("return_category", "").strip(),
        "page": request.form.get("return_page", type=int),
    }
    if return_params["tab"] not in {"manual", "approved", "not-published"}:
        return_params["tab"] = "manual"
    if return_params["rating"] not in {1, 2, 3, 4, 5}:
        return_params["rating"] = None
    if return_params["page"] is None or return_params["page"] < 1:
        return_params["page"] = 1
    try:
        actor_id = current_user.id
        revision_id = uuid.UUID(request.form.get("expected_revision_id", ""))
        action = request.form.get("action", "").strip().upper()
        if action not in {
            ReviewModerationDecisionAction.APPROVE.value,
            ReviewModerationDecisionAction.REJECT.value,
        }:
            raise ReviewModerationError("Decisión inválida.")
        db.session.remove()
        database_session = db.session()
        with database_session.begin():
            decision = apply_review_moderation_decision(
                database_session,
                review_id=review_id,
                revision_id=revision_id,
                action=action,
                source=ReviewModerationDecisionSource.MANUAL.value,
                actor_user_id=actor_id,
                idempotency_key=(request.form.get("idempotency_key") or uuid.uuid4().hex),
                reason_code=request.form.get("reason_code"),
                public_reason=request.form.get("public_reason"),
                internal_notes=request.form.get("internal_notes"),
            )
            record_admin_audit(
                database_session,
                actor_user_id=actor_id,
                action=f"REVIEW_{action}",
                reason=decision.reason_code,
                metadata={
                    "review_id": str(review_id),
                    "revision_id": str(revision_id),
                    "decision_id": str(decision.id),
                },
            )
        flash(
            "Reseña publicada." if action == "APPROVE" else "Reseña no publicada.",
            "success",
        )
    except ReviewModerationConflictError as exc:
        db.session.rollback()
        return str(exc), 409
    except (ReviewModerationError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(
        url_for(
            "admin.reviews",
            **{key: value for key, value in return_params.items() if value},
        )
    )


_ADMIN_PAYOUT_QUERY_KEYS = (
    "tab", "q", "status", "cycle", "date_from", "date_to", "store",
    "page", "per_page", "sort_by", "sort_direction", "detail",
)
_ADMIN_PAYOUT_TABS = (
    ("all", "Todas"), ("scheduled", "Programadas"),
    ("on_hold", "En hold"), ("paid", "Pagadas"),
    ("cancelled", "Canceladas"),
)


def _admin_payout_query_state() -> dict[str, str]:
    return {
        key: value
        for key in _ADMIN_PAYOUT_QUERY_KEYS
        if (value := (request.args.get(key) or "").strip())
    }


def _admin_payout_return_state(payout_number: str | None = None) -> dict[str, str]:
    values = {
        key: value
        for key in _ADMIN_PAYOUT_QUERY_KEYS
        if key != "detail" and (value := (request.form.get(key) or "").strip())
    }
    if payout_number:
        values["detail"] = payout_number
    return values


def _admin_payout_url_builder():
    base = _admin_payout_query_state()

    def build(**overrides) -> str:
        values = dict(base)
        for key, value in overrides.items():
            if key not in (*_ADMIN_PAYOUT_QUERY_KEYS, "calendar", "schedule", "cycle_date"):
                continue
            if value is None or value == "":
                values.pop(key, None)
            else:
                values[key] = str(value)
        return url_for("admin.payouts", **values)

    return build


def _admin_payout_pagination_window(page: int, pages: int) -> tuple[int | None, ...]:
    if pages <= 0:
        return ()
    if pages <= 7:
        return tuple(range(1, pages + 1))
    candidates = sorted(value for value in {1, pages, page - 1, page, page + 1} if 1 <= value <= pages)
    window: list[int | None] = []
    previous = 0
    for value in candidates:
        if previous and value - previous > 1:
            window.append(None)
        window.append(value)
        previous = value
    return tuple(window)


def _parse_cycle_date(raw: str | None) -> date:
    try:
        value = date.fromisoformat((raw or "").strip())
        payout_cycle_window(value)
        return value
    except (TypeError, ValueError) as exc:
        raise SellerPayoutEligibilityError(
            "Selecciona una fecha oficial de ciclo válida."
        ) from exc


def _parse_paid_at_local(raw: str | None) -> datetime:
    try:
        value = datetime.fromisoformat((raw or "").strip())
    except (TypeError, ValueError) as exc:
        raise SellerPayoutTransitionError("La fecha real del pago no es válida.") from exc
    if value.tzinfo is not None or value.utcoffset() is not None:
        raise SellerPayoutTransitionError("La fecha real del pago debe usar la hora de Ecuador.")
    return value.replace(tzinfo=PAYOUT_TIMEZONE).astimezone(timezone.utc)


@admin.get("/payouts")
@admin_permission_required("payouts.view")
def payouts():
    active_tab = (request.args.get("tab") or "all").strip().lower()
    query_state = _admin_payout_query_state()
    try:
        payout_list = list_admin_payouts(
            db.session,
            tab=active_tab,
            query=request.args.get("q"),
            status=request.args.get("status"),
            cycle=request.args.get("cycle"),
            date_from=request.args.get("date_from"),
            date_to=request.args.get("date_to"),
            store=request.args.get("store"),
            page=request.args.get("page", 1),
            per_page=request.args.get("per_page", 20),
            sort_by=request.args.get("sort_by", "scheduled_for"),
            sort_direction=request.args.get("sort_direction", "desc"),
        )
    except AdminPayoutQueryError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("admin.payouts", tab=active_tab if active_tab in PAYOUT_TAB_STATUSES else "all"))

    detail = None
    if detail_code := query_state.get("detail"):
        try:
            detail = get_admin_payout_detail(db.session, detail_code)
        except AdminPayoutNotFoundError:
            abort(404)

    now = datetime.now(timezone.utc)
    options = cycle_options(now=now)
    preview = None
    preview_error = None
    if request.args.get("schedule") == "1" and request.args.get("cycle_date"):
        try:
            selected_date = _parse_cycle_date(request.args.get("cycle_date"))
            preview = get_admin_cycle_preview(db.session, cycle_date=selected_date, now=now)
        except SellerPayoutError as exc:
            preview_error = str(exc)
    payout_url = _admin_payout_url_builder()
    filter_keys = {"q", "status", "cycle", "date_from", "date_to", "store"}
    return render_template(
        "admin/payouts.html",
        payouts=payout_list,
        kpis=get_admin_payout_kpis(db.session, now=now),
        detail=detail,
        active_tab=active_tab,
        payout_tabs=_ADMIN_PAYOUT_TABS,
        payout_statuses=tuple((status.value, label) for status, label in PAYOUT_STATUS_LABELS.items()),
        payout_stores=list_payout_stores(db.session),
        payout_query=query_state,
        payout_url=payout_url,
        pagination_window=_admin_payout_pagination_window(payout_list.page, payout_list.pages),
        payout_return_query_keys=tuple(key for key in _ADMIN_PAYOUT_QUERY_KEYS if key != "detail"),
        has_payout_filters=any(query_state.get(key) for key in filter_keys),
        cycle_options=options,
        next_cycle=options[0] if options else None,
        calendar_data=calendar_months(now=now),
        show_calendar=request.args.get("calendar") == "1",
        show_schedule=request.args.get("schedule") == "1",
        cycle_preview=preview,
        cycle_preview_error=preview_error,
        payout_now_local=now.astimezone(PAYOUT_TIMEZONE).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M"),
        receipt_max_mib=current_app.config["SELLER_PAYOUT_RECEIPT_MAX_BYTES"] / (1024 * 1024),
        can_schedule=user_has_permission(current_user, "payouts.schedule"),
        can_hold=user_has_permission(current_user, "payouts.hold"),
        can_cancel=user_has_permission(current_user, "payouts.cancel"),
        can_pay=user_has_permission(current_user, "payouts.pay"),
        can_view_proof=user_has_permission(current_user, "payouts.proof.view"),
        **_shell_context("payouts"),
    )


def _payout_mutation_redirect(payout_number: str | None = None):
    return redirect(url_for("admin.payouts", **_admin_payout_return_state(payout_number)))


def _payout_transition(action, payout_number: str, success: str, replay: str):
    try:
        result = action(db.session, payout_number=payout_number, actor_user_id=current_user.id)
        db.session.commit()
    except SellerPayoutNotFoundError:
        db.session.rollback()
        abort(404)
    except SellerPayoutTransitionError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    else:
        flash(replay if result.replayed else success, "info" if result.replayed else "success")
    return _payout_mutation_redirect(payout_number)


@admin.post("/payouts/<string:payout_number>/hold")
@limiter.limit("30 per hour")
@admin_permission_required("payouts.hold")
def hold_payout(payout_number: str):
    return _payout_transition(hold_seller_payout, payout_number, "Liquidación puesta en hold.", "La liquidación ya estaba en hold.")


@admin.post("/payouts/<string:payout_number>/resume")
@limiter.limit("30 per hour")
@admin_permission_required("payouts.hold")
def resume_payout(payout_number: str):
    return _payout_transition(resume_seller_payout, payout_number, "Liquidación reanudada.", "La liquidación ya estaba programada.")


@admin.post("/payouts/<string:payout_number>/cancel")
@limiter.limit("30 per hour")
@admin_permission_required("payouts.cancel")
def cancel_payout(payout_number: str):
    def cancel_action(session, *, payout_number, actor_user_id):
        return cancel_seller_payout(
            session, payout_number=payout_number,
            cancelled_at=datetime.now(timezone.utc), actor_user_id=actor_user_id,
        )
    return _payout_transition(cancel_action, payout_number, "Liquidación cancelada; sus pedidos fueron liberados.", "La liquidación ya estaba cancelada.")


@admin.post("/payouts/schedule")
@limiter.limit("10 per hour")
@admin_permission_required("payouts.schedule")
def schedule_payouts():
    try:
        cycle_date = _parse_cycle_date(request.form.get("cycle_date"))
        result = schedule_payout_cycle(
            db.session, cycle_date=cycle_date, now=datetime.now(timezone.utc),
            actor_user_id=current_user.id,
        )
        db.session.commit()
    except SellerPayoutError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
    else:
        count = len(result.payouts)
        flash(f"Ciclo {cycle_date:%d %b %Y} programado: {count} liquidaciones creadas.", "success")
        if result.skipped_store_ids:
            flash(f"{len(result.skipped_store_ids)} tiendas no pudieron incluirse; revisa su elegibilidad.", "warning")
    values = _admin_payout_return_state()
    values["tab"] = "scheduled"
    return redirect(url_for("admin.payouts", **values))


@admin.post("/payouts/<string:payout_number>/pay")
@limiter.limit("20 per hour")
@admin_permission_required("payouts.pay")
def pay_payout(payout_number: str):
    staged = None
    promoted_path = None
    receipt_root = current_app.config["SELLER_PAYOUT_RECEIPT_DIR"]
    try:
        paid_at = _parse_paid_at_local(request.form.get("paid_at"))
        upload = request.files.get("receipt")
        if upload is not None and upload.filename:
            staged = stage_private_upload(
                upload, root=receipt_root,
                max_bytes=current_app.config["SELLER_PAYOUT_RECEIPT_MAX_BYTES"],
                allowed_extensions={"jpg", "jpeg", "png", "pdf"},
                storage_prefix="payout-receipts",
            )
            promoted_path = private_file_path(receipt_root, staged.storage_key)
        result = mark_seller_payout_paid(
            db.session, payout_number=payout_number,
            external_reference=request.form.get("external_reference") or "",
            paid_at=paid_at, actor_user_id=current_user.id,
            staged_receipt=staged, receipt_root=receipt_root,
        )
        db.session.commit()
    except SellerPayoutNotFoundError:
        db.session.rollback()
        if staged is not None:
            delete_private_file(staged.temporary_path)
        if promoted_path is not None:
            delete_private_file(promoted_path)
        abort(404)
    except (SellerPayoutTransitionError, PrivateStorageError) as exc:
        db.session.rollback()
        if staged is not None:
            delete_private_file(staged.temporary_path)
        if promoted_path is not None:
            delete_private_file(promoted_path)
        flash(str(exc), "warning")
    except Exception:
        db.session.rollback()
        if staged is not None:
            delete_private_file(staged.temporary_path)
        if promoted_path is not None:
            delete_private_file(promoted_path)
        raise
    else:
        flash("La liquidación ya estaba pagada." if result.replayed else "Liquidación marcada como pagada.", "info" if result.replayed else "success")
    return _payout_mutation_redirect(payout_number)


@admin.get("/payouts/<string:payout_number>/receipt")
@admin_permission_required("payouts.proof.view")
def payout_receipt(payout_number: str):
    normalized = " ".join((payout_number or "").split()).upper()
    payout = db.session.scalar(select(SellerPayout).where(SellerPayout.payout_number == normalized))
    if payout is None or not payout.receipt_storage_key:
        abort(404)
    try:
        path = verify_private_file(
            root=current_app.config["SELLER_PAYOUT_RECEIPT_DIR"],
            storage_key=payout.receipt_storage_key,
            size_bytes=payout.receipt_size_bytes,
            sha256=payout.receipt_sha256,
        )
    except PrivateStorageError:
        abort(404)
    response = send_file(
        path, mimetype=payout.receipt_media_type,
        as_attachment=request.args.get("download") == "1",
        download_name=PurePath(payout.receipt_original_filename or "comprobante").name,
        conditional=False, max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


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
