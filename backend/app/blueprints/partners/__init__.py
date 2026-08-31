from __future__ import annotations

import base64
import csv
import io
import uuid
from urllib.parse import urlencode

from flask import (
    Blueprint,
    Response,
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
from flask_login import current_user, login_required
from werkzeug.exceptions import NotFound

from app.extensions import db, limiter
from app.models.enums import ProductDraftFileKind, ProductDraftFileStatus, StoreOnboardingStatus
from app.services.partner_onboarding import (
    CORRECTION_REASON_LABELS,
    DOCUMENT_TYPES,
    PartnerOnboardingError,
    PartnerOnboardingValidationError,
    STEPS,
    accept_contract,
    bank_correction_requires_replacement,
    contract_pdf_bytes,
    get_or_create_onboarding,
    get_onboarding,
    latest_correction_review,
    request_contract_otp,
    save_step,
    stage_partner_document,
    submit_for_review,
    unresolved_correction_issues,
)
from app.services.bank_accounts import (
    bank_account_summary,
    onboarding_bank_account_version,
)
from app.services.partner_product_categories import (
    PARTNER_PRODUCT_DRAFT_SESSION_KEY,
    PartnerProductAccessError,
    PartnerProductCategoryValidationError,
    get_category_selection_page,
    get_saved_category_selection,
    require_partner_catalog_store,
    save_product_category_selection,
    validate_category_selection,
)
from app.services.partner_product_catalog import get_partner_product_catalog
from app.services.partner_product_actions import (
    prepare_product_draft_deletion,
    submit_product_draft_batch,
)
from app.services.private_storage import (
    PrivateStorageError,
    private_file_path,
    verify_private_file,
)
from app.services.barcodes import (
    BarcodeRenderError,
    render_package_code128_svg,
    render_product_code128_svg,
)
from app.services.product_drafts import (
    PARTNER_CURRENT_PRODUCT_DRAFT_SESSION_KEY,
    ProductDraftAccessError,
    ProductDraftError,
    ProductDraftStateError,
    ProductDraftValidationError,
    assign_product_draft_image,
    attach_product_draft_file,
    attach_product_draft_files,
    build_product_draft_view,
    create_or_reuse_draft_from_selection,
    delete_product_draft_file,
    delete_product_draft_color_media,
    draft_commission_display_rows,
    get_product_draft_for_user,
    reorder_product_draft_images,
    save_product_draft,
    set_cover_image,
    stage_product_draft_upload,
    submit_saved_product_draft,
)
from app.services.product_draft_preview import build_product_draft_preview
from app.services.partner_reviews import (
    PartnerReviewAccessError,
    PartnerReviewConflictError,
    PartnerReviewValidationError,
    get_partner_reviews_page,
    save_partner_review_reply,
)
from app.services.partner_orders import (
    PartnerOrderAccessError,
    PartnerOrderConflictError,
    PartnerOrderNotFoundError,
    PartnerOrderValidationError,
    approve_partner_order,
    get_partner_order_detail,
    get_partner_orders_page,
    reject_partner_order,
)
from app.services.partner_sales import (
    PartnerPayoutNotFoundError,
    PartnerSalesAccessError,
    get_authorized_payout_receipt,
    get_partner_payout_detail,
    get_partner_sales_export,
    get_partner_sales_page,
)
from app.services.seller_inbound_packages import (
    create_partner_inbound_package,
    get_partner_inbound_package_label,
    mark_partner_inbound_package_ready,
)


partners = Blueprint("partners", __name__, url_prefix="/partners")


def _get_or_create_for_current_user():
    onboarding = get_onboarding(db.session, current_user.id)
    if onboarding is not None:
        return onboarding
    onboarding = get_or_create_onboarding(db.session, current_user.id)
    db.session.commit()
    return onboarding


def _render_main(onboarding, *, step: int | None = None, errors=None, form=None, status_code=200):
    bank_version = onboarding_bank_account_version(db.session, onboarding)
    if form is not None:
        form_values = {key: form.get(key, "") for key in (*STEPS[1].fields, *STEPS[2].fields, *STEPS[3].fields, "bank_account_owner", "bank_name", "bank_id_number", "bank_email", "bank_action")}
        # The full account number is write-only and is never reflected into HTML.
        form_values["bank_account_number"] = ""
    else:
        form_values = {
        "store_name": onboarding.store_name or "",
        "legal_id_number": onboarding.legal_id_number or "",
        "province": onboarding.province or "",
        "city": onboarding.city or "",
        "address": onboarding.address or "",
        "whatsapp_or_nickname": onboarding.whatsapp_or_nickname or "",
        "bank_account_owner": "",
        "bank_account_number": "",
        "bank_name": "",
        "bank_id_number": "",
        "bank_email": onboarding.bank_email or "",
        "bank_action": "keep" if bank_version is not None else "replace",
        }
    replacement_required = bank_correction_requires_replacement(
        onboarding,
        bank_version,
    )
    return render_template(
        "partners/onboarding.html",
        onboarding=onboarding,
        steps=STEPS,
        step=step or onboarding.current_step,
        errors=errors or {},
        form=form_values,
        bank_summary=bank_account_summary(bank_version),
        bank_replacement_required=replacement_required,
        current_partner_tab="main",
        document_types=DOCUMENT_TYPES,
        correction_reason_labels=CORRECTION_REASON_LABELS,
        correction_documents={str(document.id): document for document in onboarding.documents},
        correction_review=latest_correction_review(onboarding),
        pending_corrections=unresolved_correction_issues(
            onboarding,
            bank_version=bank_version,
        ),
    ), status_code


@partners.get("")
@login_required
def dashboard():
    onboarding = _get_or_create_for_current_user()
    if onboarding.status == StoreOnboardingStatus.COMPLETED:
        return redirect(url_for("partners.products"))
    if onboarding.status in {
        StoreOnboardingStatus.SUBMITTED,
        StoreOnboardingStatus.CORRECTIONS_REQUESTED,
        StoreOnboardingStatus.APPROVED,
        StoreOnboardingStatus.REJECTED,
    }:
        return redirect(url_for("partners.status"))
    if onboarding.status == StoreOnboardingStatus.CONTRACT_PENDING:
        return redirect(url_for("partners.contract"))
    return redirect(url_for("partners.onboarding_step", step=onboarding.current_step))


@partners.get("/onboarding")
@login_required
def onboarding_home():
    onboarding = _get_or_create_for_current_user()
    return redirect(url_for("partners.onboarding_step", step=onboarding.current_step))


@partners.get("/onboarding/step/<int:step>")
@login_required
def onboarding_step(step: int):
    onboarding = _get_or_create_for_current_user()
    if step not in STEPS:
        return redirect(url_for("partners.onboarding_step", step=onboarding.current_step))
    if (
        onboarding.status != StoreOnboardingStatus.CORRECTIONS_REQUESTED
        and step > onboarding.current_step
    ):
        flash("Completa los pasos anteriores antes de continuar.", "warning")
        return redirect(url_for("partners.onboarding_step", step=onboarding.current_step))
    return _render_main(onboarding, step=step)[0]


@partners.post("/onboarding/step/<int:step>")
@login_required
def save_onboarding_step(step: int):
    user_id = current_user.id
    staged = []
    storage_root = current_app.config["PARTNER_DOCUMENT_UPLOAD_DIR"]
    try:
        if step == 4:
            for uploaded in request.files.getlist("documents"):
                if uploaded and uploaded.filename:
                    staged.append(
                        stage_partner_document(
                            uploaded,
                            root=storage_root,
                            max_bytes=current_app.config["PARTNER_DOCUMENT_MAX_BYTES"],
                        )
                    )
        db.session.remove()
        with db.session.begin():
            onboarding = save_step(
                session=db.session,
                user_id=user_id,
                step=step,
                data=request.form,
                staged_documents=tuple(staged),
                storage_root=storage_root,
            )
        next_step = min(step + 1, 5)
        if step == 5:
            return redirect(url_for("partners.review"))
        return redirect(url_for("partners.onboarding_step", step=next_step))
    except PartnerOnboardingValidationError as exc:
        db.session.rollback()
        for item in staged:
            item.temporary_path.unlink(missing_ok=True)
        onboarding = _get_or_create_for_current_user()
        return _render_main(onboarding, step=step, errors=exc.errors, form=request.form, status_code=400)
    except PartnerOnboardingError as exc:
        db.session.rollback()
        for item in staged:
            item.temporary_path.unlink(missing_ok=True)
        flash(str(exc), "error")
        return redirect(url_for("partners.dashboard"))


@partners.get("/onboarding/review")
@login_required
def review():
    onboarding = _get_or_create_for_current_user()
    return render_template(
        "partners/review.html",
        onboarding=onboarding,
        current_partner_tab="main",
    )


@partners.post("/onboarding/review")
@login_required
def submit_review():
    user_id = current_user.id
    try:
        db.session.remove()
        with db.session.begin():
            submit_for_review(db.session, user_id)
        flash("Solicitud enviada para verificación.", "success")
        return redirect(url_for("partners.status"))
    except PartnerOnboardingError as exc:
        flash(str(exc), "error")
        return redirect(url_for("partners.review"))


@partners.get("/onboarding/status")
@login_required
def status():
    onboarding = _get_or_create_for_current_user()
    bank_version = onboarding_bank_account_version(db.session, onboarding)
    return render_template(
        "partners/status.html",
        onboarding=onboarding,
        document_types=DOCUMENT_TYPES,
        correction_reason_labels=CORRECTION_REASON_LABELS,
        correction_documents={str(document.id): document for document in onboarding.documents},
        correction_review=latest_correction_review(onboarding),
        pending_corrections=unresolved_correction_issues(
            onboarding,
            bank_version=bank_version,
        ),
        current_partner_tab="main",
    )


@partners.get("/contract")
@login_required
def contract():
    onboarding = get_onboarding(db.session, current_user.id)
    if onboarding is None:
        return redirect(url_for("partners.dashboard"))
    return render_template(
        "partners/contract.html",
        onboarding=onboarding,
        current_partner_tab="main",
    )


@partners.get("/contract/pdf")
@login_required
def contract_pdf():
    onboarding = get_onboarding(db.session, current_user.id)
    if onboarding is None:
        return redirect(url_for("partners.dashboard"))
    if onboarding.status not in {
        StoreOnboardingStatus.APPROVED,
        StoreOnboardingStatus.CONTRACT_PENDING,
        StoreOnboardingStatus.COMPLETED,
    }:
        flash("El contrato estará disponible cuando la tienda sea aprobada.", "warning")
        return redirect(url_for("partners.status"))
    return Response(
        contract_pdf_bytes(onboarding),
        mimetype="application/pdf",
        headers={"Content-Disposition": "attachment; filename=contrato-ecuvel-partners.pdf"},
    )


@partners.post("/contract/otp")
@login_required
@limiter.limit(lambda: current_app.config["PARTNER_CONTRACT_OTP_RATE_LIMIT"])
def contract_otp():
    user_id = current_user.id
    action = request.form.get("action")
    try:
        if action == "send":
            db.session.remove()
            with db.session.begin():
                challenge = request_contract_otp(db.session, user_id)
            flash(f"Enviamos un código a {challenge.destination_masked}.", "success")
        else:
            db.session.remove()
            with db.session.begin():
                accept_contract(
                    session=db.session,
                    user_id=user_id,
                    code=request.form.get("otp_code", ""),
                    declarations=request.form,
                    ip_address=request.remote_addr,
                    user_agent=request.user_agent.string,
                    storage_root=current_app.config["PARTNER_CONTRACT_UPLOAD_DIR"],
                )
            flash("Contrato aceptado correctamente.", "success")
            return redirect(url_for("partners.products"))
    except PartnerOnboardingError as exc:
        flash(str(exc), "error")
    return redirect(url_for("partners.contract"))


@partners.get("/contract/accepted.pdf")
@login_required
def accepted_contract_pdf():
    onboarding = get_onboarding(db.session, current_user.id)
    acceptance = onboarding.contract_acceptance if onboarding else None
    if acceptance is None or not acceptance.pdf_storage_key:
        flash("Aún no existe un contrato aceptado.", "warning")
        return redirect(url_for("partners.contract"))
    path = private_file_path(current_app.config["PARTNER_CONTRACT_UPLOAD_DIR"], acceptance.pdf_storage_key)
    return send_file(path, mimetype="application/pdf", as_attachment=True, download_name="contrato-aceptado-ecuvel-partners.pdf")


@partners.get("/products")
@login_required
def products():
    try:
        store = require_partner_catalog_store(db.session, current_user.id)
    except PartnerProductAccessError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("partners.dashboard"))
    return render_template(
        "partners/products.html",
        store=store,
        current_partner_tab="products",
    )


@partners.get("/my-products")
@login_required
def my_products():
    try:
        store = require_partner_catalog_store(db.session, current_user.id)
    except PartnerProductAccessError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("partners.dashboard"))
    catalog = get_partner_product_catalog(
        db.session,
        store_id=store.store_id,
        store_name=store.store_name,
        query=request.args.get("q"),
        status=request.args.get("status"),
        category=request.args.get("category"),
        page=request.args.get("page"),
    )
    return render_template(
        "partners/my_products.html",
        catalog=catalog,
        batch_result=browser_session.pop("partner_catalog_batch_result", None),
        current_partner_tab="my_products",
    )


@partners.get("/orders")
@login_required
def partner_orders():
    try:
        page = get_partner_orders_page(
            db.session,
            user_id=current_user.id,
            tab=request.args.get("tab"),
            query=request.args.get("q"),
            status=request.args.get("status"),
            date_filter=request.args.get("date"),
            page=request.args.get("page"),
        )
    except PartnerOrderAccessError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("partners.dashboard"))
    return render_template(
        "partners/orders.html",
        page=page,
        current_partner_tab="orders",
    )


@partners.get("/sales")
@login_required
def partner_sales():
    try:
        page = get_partner_sales_page(
            db.session,
            user_id=current_user.id,
            period_key=request.args.get("period"),
            placeholder_image=url_for(
                "static", filename="images/placeholders/product-placeholder.svg"
            ),
        )
    except PartnerSalesAccessError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("partners.dashboard"))
    return render_template(
        "partners/sales.html",
        page=page,
        current_partner_tab="sales",
    )


@partners.get("/sales/export.csv")
@login_required
def partner_sales_export():
    try:
        period, rows = get_partner_sales_export(
            db.session,
            user_id=current_user.id,
            period_key=request.args.get("period"),
        )
    except PartnerSalesAccessError as exc:
        raise NotFound(str(exc)) from exc
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "SellerOrder",
            "Fecha de pago del comprador",
            "Productos",
            "Unidades",
            "Subtotal",
            "Descuentos",
            "Comisión ECUVEL",
            "Neto",
            "Estado logístico",
            "Estado de liquidación",
            "Fecha de elegibilidad",
            "Referencia payout",
            "Fecha de pago ECUVEL",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                _csv_safe(row.seller_order_number),
                row.approved_at.isoformat(),
                _csv_safe(row.products),
                row.units,
                f"{row.subtotal:.2f}",
                f"{row.discounts:.2f}",
                f"{row.commission:.2f}",
                f"{row.net:.2f}",
                _csv_safe(row.logistics_status),
                _csv_safe(row.payout_status),
                row.eligible_at.isoformat() if row.eligible_at else "",
                _csv_safe(row.payout_reference or ""),
                row.paid_at.isoformat() if row.paid_at else "",
            ]
        )
    return Response(
        "\ufeff" + buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="ventas-{period.key}.csv"'
        },
    )


@partners.get("/sales/payouts/<uuid:payout_id>/detail")
@login_required
def partner_sales_payout_detail(payout_id):
    try:
        detail = get_partner_payout_detail(
            db.session,
            user_id=current_user.id,
            payout_id=payout_id,
        )
    except (PartnerSalesAccessError, PartnerPayoutNotFoundError) as exc:
        raise NotFound(str(exc)) from exc
    return jsonify(
        {
            "ok": True,
            "payout": {
                "id": str(detail.payout_id),
                "reference": detail.reference,
                "status": detail.status,
                "status_label": detail.status_label,
                "status_tone": detail.status_tone,
                "date_label": detail.date_label,
                "destination_label": detail.destination_label,
                "order_count": detail.order_count,
                "currency": detail.currency,
                "gross_sales_total": _money_json(detail.gross_sales_total),
                "discount_total": _money_json(detail.discount_total),
                "commission_total": _money_json(detail.commission_total),
                "net_total": _money_json(detail.net_total),
                "receipt_available": detail.receipt_available,
                "receipt_url": (
                    url_for(
                        "partners.partner_sales_payout_receipt",
                        payout_id=detail.payout_id,
                    )
                    if detail.receipt_available
                    else None
                ),
            },
        }
    )


@partners.get("/sales/payouts/<uuid:payout_id>/receipt")
@login_required
def partner_sales_payout_receipt(payout_id):
    try:
        payout = get_authorized_payout_receipt(
            db.session,
            user_id=current_user.id,
            payout_id=payout_id,
        )
        path = verify_private_file(
            root=current_app.config["SELLER_PAYOUT_RECEIPT_DIR"],
            storage_key=payout.receipt_storage_key,
            size_bytes=payout.receipt_size_bytes,
            sha256=payout.receipt_sha256,
        )
    except (
        PartnerSalesAccessError,
        PartnerPayoutNotFoundError,
        PrivateStorageError,
    ) as exc:
        raise NotFound(str(exc)) from exc
    return send_file(
        path,
        mimetype=payout.receipt_media_type,
        as_attachment=True,
        download_name=(
            payout.receipt_original_filename or f"{payout.payout_number}.pdf"
        ),
    )


@partners.get("/orders/<uuid:seller_order_id>/detail")
@login_required
def partner_order_detail(seller_order_id):
    try:
        detail = get_partner_order_detail(
            db.session,
            user_id=current_user.id,
            seller_order_id=seller_order_id,
            buyer_pickup_point_name=current_app.config["ECUVEL_PICKUP_POINT_NAME"],
            buyer_pickup_point_address=current_app.config["ECUVEL_PICKUP_POINT_ADDRESS"],
            placeholder_image=url_for(
                "static", filename="images/placeholders/product-placeholder.svg"
            ),
        )
    except (PartnerOrderAccessError, PartnerOrderNotFoundError) as exc:
        raise NotFound(str(exc)) from exc
    return jsonify({"ok": True, "order": _partner_order_detail_json(detail)})


@partners.post("/orders/<uuid:seller_order_id>/packages")
@login_required
@limiter.limit("30 per minute")
def partner_order_create_package(seller_order_id):
    try:
        result = create_partner_inbound_package(
            db.session,
            user_id=current_user.id,
            seller_order_id=seller_order_id,
        )
        db.session.commit()
    except PartnerOrderValidationError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 422
    except (PartnerOrderAccessError, PartnerOrderNotFoundError) as exc:
        db.session.rollback()
        raise NotFound(str(exc)) from exc
    except PartnerOrderConflictError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify(
        {
            "ok": True,
            "package": {
                "package_id": str(result.package_id),
                "package_code": result.package_code,
                "barcode": result.barcode,
                "status": result.status.value,
                "status_label": result.status_label,
            },
        }
    ), 201


@partners.post(
    "/orders/<uuid:seller_order_id>/packages/<uuid:package_id>/ready"
)
@login_required
@limiter.limit("30 per minute")
def partner_order_package_ready(seller_order_id, package_id):
    try:
        result = mark_partner_inbound_package_ready(
            db.session,
            user_id=current_user.id,
            seller_order_id=seller_order_id,
            package_id=package_id,
        )
        db.session.commit()
    except (PartnerOrderAccessError, PartnerOrderNotFoundError) as exc:
        db.session.rollback()
        raise NotFound(str(exc)) from exc
    except PartnerOrderConflictError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify(
        {
            "ok": True,
            "package": {
                "package_id": str(result.package_id),
                "package_code": result.package_code,
                "status": result.status.value,
                "status_label": result.status_label,
                "replayed": result.replayed,
            },
        }
    )


@partners.get(
    "/orders/<uuid:seller_order_id>/packages/<uuid:package_id>/label"
)
@login_required
def partner_order_package_label(seller_order_id, package_id):
    try:
        label = get_partner_inbound_package_label(
            db.session,
            user_id=current_user.id,
            seller_order_id=seller_order_id,
            package_id=package_id,
        )
        barcode_svg = render_package_code128_svg(label.barcode)
    except (PartnerOrderAccessError, PartnerOrderNotFoundError) as exc:
        raise NotFound(str(exc)) from exc
    except BarcodeRenderError as exc:
        return Response(str(exc), status=422, mimetype="text/plain")
    return render_template(
        "partners/order_package_label.html",
        label=label,
        barcode_data=base64.b64encode(barcode_svg).decode("ascii"),
    )


@partners.post("/orders/<uuid:seller_order_id>/approve")
@login_required
@limiter.limit("30 per minute")
def partner_order_approve(seller_order_id):
    try:
        result = approve_partner_order(
            db.session,
            user_id=current_user.id,
            seller_order_id=seller_order_id,
        )
        db.session.commit()
    except (PartnerOrderAccessError, PartnerOrderNotFoundError) as exc:
        db.session.rollback()
        raise NotFound(str(exc)) from exc
    except PartnerOrderConflictError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify({"ok": True, **_partner_order_decision_json(result)})


@partners.post("/orders/<uuid:seller_order_id>/reject")
@login_required
@limiter.limit("30 per minute")
def partner_order_reject(seller_order_id):
    try:
        result = reject_partner_order(
            db.session,
            user_id=current_user.id,
            seller_order_id=seller_order_id,
            reason=request.form.get("reason"),
            comment=request.form.get("comment"),
        )
        db.session.commit()
    except PartnerOrderValidationError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 422
    except (PartnerOrderAccessError, PartnerOrderNotFoundError) as exc:
        db.session.rollback()
        raise NotFound(str(exc)) from exc
    except PartnerOrderConflictError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 409
    return jsonify({"ok": True, **_partner_order_decision_json(result)})


@partners.get("/reviews")
@login_required
def partner_reviews():
    try:
        page = get_partner_reviews_page(
            db.session,
            user_id=current_user.id,
            query=request.args.get("q"),
            statuses=request.args.getlist("status"),
            ratings=request.args.getlist("rating"),
            sort=request.args.get("sort"),
            page=request.args.get("page"),
        )
    except PartnerReviewAccessError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("partners.dashboard"))
    return render_template(
        "partners/reviews.html",
        page=page,
        previous_url=_partner_reviews_page_url(page.page - 1) if page.has_previous else None,
        next_url=_partner_reviews_page_url(page.page + 1) if page.has_next else None,
        current_partner_tab="reviews",
    )


@partners.post("/reviews/<uuid:review_id>/reply")
@login_required
@limiter.limit("30 per minute")
def partner_review_reply(review_id):
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )
    try:
        result = save_partner_review_reply(
            db.session,
            user_id=current_user.id,
            review_id=review_id,
            body=request.form.get("body"),
            expected_updated_at=request.form.get("expected_updated_at"),
        )
        db.session.commit()
    except PartnerReviewValidationError as exc:
        db.session.rollback()
        if wants_json:
            return jsonify({"ok": False, "error": str(exc)}), 422
        flash(str(exc), "error")
        return _partner_reviews_form_redirect(review_id)
    except PartnerReviewConflictError as exc:
        db.session.rollback()
        if wants_json:
            return jsonify({"ok": False, "error": str(exc), "conflict": True}), 409
        flash(str(exc), "error")
        return _partner_reviews_form_redirect(review_id)
    except PartnerReviewAccessError as exc:
        db.session.rollback()
        raise NotFound(str(exc)) from exc

    if wants_json:
        return jsonify(
            {
                "ok": True,
                "created": result.created,
                "review_id": str(result.review_id),
                "reply": {
                    "body": result.reply.body,
                    "created_date_label": result.reply.created_date_label,
                    "updated_date_label": result.reply.updated_date_label,
                    "is_edited": result.reply.is_edited,
                    "version": result.reply.version,
                },
                "metrics": {
                    "total_reviews": result.metrics.total_reviews,
                    "answered_reviews": result.metrics.answered_reviews,
                    "unanswered_reviews": result.metrics.unanswered_reviews,
                    "response_rate": result.metrics.response_rate,
                },
            }
        )
    flash("Respuesta publicada correctamente.", "success")
    return _partner_reviews_form_redirect(review_id)


@partners.post("/products/drafts/<uuid:draft_id>/delete")
@login_required
def delete_product_draft_route(draft_id):
    prepared = None
    try:
        db.session.remove()
        with db.session.begin():
            prepared = prepare_product_draft_deletion(
                db.session,
                user_id=current_user.id,
                draft_ids=[draft_id],
                root=current_app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"],
            )
        prepared.finalize()
        if browser_session.get(PARTNER_CURRENT_PRODUCT_DRAFT_SESSION_KEY) == str(draft_id):
            browser_session.pop(PARTNER_CURRENT_PRODUCT_DRAFT_SESSION_KEY, None)
        flash("Borrador eliminado permanentemente.", "success")
    except PartnerProductAccessError as exc:
        if prepared:
            prepared.restore()
        raise NotFound(str(exc)) from exc
    except ProductDraftAccessError as exc:
        if prepared:
            prepared.restore()
        raise NotFound(str(exc)) from exc
    except ProductDraftStateError as exc:
        if prepared:
            prepared.restore()
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        if prepared:
            prepared.restore()
        db.session.rollback()
        raise
    return _my_products_redirect()


@partners.post("/my-products/bulk/delete")
@login_required
def bulk_delete_product_drafts_route():
    prepared = None
    try:
        db.session.remove()
        with db.session.begin():
            prepared = prepare_product_draft_deletion(
                db.session,
                user_id=current_user.id,
                draft_ids=request.form.getlist("draft_ids"),
                root=current_app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"],
            )
        prepared.finalize()
        deleted_ids = {str(draft_id) for draft_id in prepared.summary.draft_ids}
        if browser_session.get(PARTNER_CURRENT_PRODUCT_DRAFT_SESSION_KEY) in deleted_ids:
            browser_session.pop(PARTNER_CURRENT_PRODUCT_DRAFT_SESSION_KEY, None)
        count = len(prepared.summary.draft_ids)
        flash(
            f"{count} {'borrador eliminado' if count == 1 else 'borradores eliminados'} permanentemente.",
            "success",
        )
    except (PartnerProductAccessError, ProductDraftAccessError) as exc:
        if prepared:
            prepared.restore()
        raise NotFound(str(exc)) from exc
    except ProductDraftStateError as exc:
        if prepared:
            prepared.restore()
        db.session.rollback()
        flash(str(exc), "error")
    except Exception:
        if prepared:
            prepared.restore()
        db.session.rollback()
        raise
    return _my_products_redirect()


@partners.post("/my-products/bulk/submit")
@login_required
@limiter.limit("12 per hour")
def bulk_submit_product_drafts_route():
    try:
        db.session.remove()
        with db.session.begin():
            result = submit_product_draft_batch(
                db.session,
                user_id=current_user.id,
                draft_ids=request.form.getlist("draft_ids"),
            )
        browser_session["partner_catalog_batch_result"] = {
            "kind": "submit",
            "submitted_count": len(result.submitted_ids),
            "failures": [
                {
                    "draft_id": str(failure.draft_id),
                    "title": failure.title,
                    "message": failure.message,
                }
                for failure in result.failures
            ],
        }
        if result.submitted_ids:
            flash(
                f"{len(result.submitted_ids)} "
                f"{'producto enviado' if len(result.submitted_ids) == 1 else 'productos enviados'} a revisión.",
                "success",
            )
    except (PartnerProductAccessError, ProductDraftAccessError) as exc:
        raise NotFound(str(exc)) from exc
    except ProductDraftStateError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _my_products_redirect()


@partners.get("/products/new")
@login_required
def new_product():
    return redirect(url_for("partners.new_product_category"))


@partners.get("/products/new/category")
@login_required
def new_product_category():
    try:
        page = get_category_selection_page(
            db.session,
            current_user.id,
            browser_session.get(PARTNER_PRODUCT_DRAFT_SESSION_KEY),
        )
    except PartnerProductAccessError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("partners.dashboard"))
    return render_template(
        "partners/product_category.html",
        page=page,
        categories_json=_category_json(page.categories),
        errors={},
        current_partner_tab="products",
    )


@partners.post("/products/new/category")
@partners.post("/products/drafts")
@login_required
def create_product_draft():
    try:
        result = validate_category_selection(
            db.session,
            user_id=current_user.id,
            category_id=request.form.get("category_id"),
            subcategory_id=request.form.get("subcategory_id"),
        )
        save_product_category_selection(browser_session, result)
        db.session.remove()
        with db.session.begin():
            draft = create_or_reuse_draft_from_selection(
                db.session,
                user_id=current_user.id,
                browser_session=browser_session,
            )
        return redirect(url_for("partners.product_draft", draft_id=draft.id))
    except PartnerProductAccessError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("partners.dashboard"))
    except PartnerProductCategoryValidationError as exc:
        page = get_category_selection_page(
            db.session,
            current_user.id,
            {
                "category_id": request.form.get("category_id") or "",
                "subcategory_id": request.form.get("subcategory_id") or "",
            },
        )
        return render_template(
            "partners/product_category.html",
            page=page,
            categories_json=_category_json(page.categories),
            errors=exc.errors,
            current_partner_tab="products",
        ), 400


@partners.get("/products/new/details")
@login_required
def new_product_details():
    current_id = browser_session.get(PARTNER_CURRENT_PRODUCT_DRAFT_SESSION_KEY)
    if current_id:
        return redirect(url_for("partners.product_draft", draft_id=current_id))
    try:
        selection = get_saved_category_selection(
            db.session,
            current_user.id,
            browser_session.get(PARTNER_PRODUCT_DRAFT_SESSION_KEY),
        )
    except PartnerProductAccessError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("partners.dashboard"))
    except PartnerProductCategoryValidationError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("partners.new_product_category"))
    return render_template(
        "partners/product_details_placeholder.html",
        selection=selection,
        current_partner_tab="products",
    )


@partners.get("/products/drafts/<uuid:draft_id>")
@login_required
def product_draft(draft_id):
    try:
        draft = get_product_draft_for_user(db.session, user_id=current_user.id, draft_id=draft_id)
    except ProductDraftAccessError as exc:
        raise NotFound(str(exc)) from exc
    return render_template(
        "partners/product_draft_form.html",
        view=build_product_draft_view(draft),
        errors={},
        current_partner_tab="products",
    )


@partners.post("/products/drafts/<uuid:draft_id>/save")
@login_required
def save_product_draft_route(draft_id):
    try:
        db.session.remove()
        with db.session.begin():
            draft = save_product_draft(
                db.session,
                user_id=current_user.id,
                draft_id=draft_id,
                form=request.form,
                final=False,
            )
        if _wants_json():
            payload = _product_draft_gallery_payload(draft.id)
            payload.update({
                "draft_id": str(draft.id),
                "completion_percentage": draft.completion_percentage,
            })
            return payload
        flash("Borrador guardado.", "success")
        return redirect(url_for("partners.product_draft", draft_id=draft.id))
    except ProductDraftValidationError as exc:
        db.session.rollback()
        draft = get_product_draft_for_user(db.session, user_id=current_user.id, draft_id=draft_id)
        if _wants_json():
            return {"ok": False, "errors": exc.errors}, 422
        return render_template(
            "partners/product_draft_form.html",
            view=build_product_draft_view(draft),
            errors=exc.errors,
            current_partner_tab="products",
        ), 400
    except ProductDraftAccessError as exc:
        raise NotFound(str(exc)) from exc
    except ProductDraftStateError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("partners.product_draft", draft_id=draft_id))


@partners.post("/products/drafts/<uuid:draft_id>/submit")
@login_required
@limiter.limit("12 per hour")
def submit_product_draft_route(draft_id):
    try:
        db.session.remove()
        with db.session.begin():
            draft = save_product_draft(
                db.session,
                user_id=current_user.id,
                draft_id=draft_id,
                form=request.form,
                final=True,
            )
        flash("Producto enviado a revisión. Aún no está publicado.", "success")
        if _wants_json():
            return {"ok": True, "draft_id": str(draft.id), "status": draft.status.value}
        return redirect(url_for("partners.product_draft_preview", draft_id=draft.id))
    except ProductDraftValidationError as exc:
        db.session.rollback()
        draft = get_product_draft_for_user(db.session, user_id=current_user.id, draft_id=draft_id)
        if _wants_json():
            return {"ok": False, "errors": exc.errors}, 422
        return render_template(
            "partners/product_draft_form.html",
            view=build_product_draft_view(draft),
            errors=exc.errors,
            current_partner_tab="products",
        ), 400
    except ProductDraftAccessError as exc:
        raise NotFound(str(exc)) from exc
    except ProductDraftStateError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("partners.product_draft", draft_id=draft_id))


@partners.get("/products/drafts/<uuid:draft_id>/preview")
@login_required
def product_draft_preview(draft_id):
    try:
        draft = get_product_draft_for_user(db.session, user_id=current_user.id, draft_id=draft_id)
    except ProductDraftAccessError as exc:
        raise NotFound(str(exc)) from exc
    view = build_product_draft_view(draft)
    preview = build_product_draft_preview(
        view,
        requested_sku=request.args.get("variant"),
        selected_view=request.args.get("view"),
    )
    return render_template(
        "partners/product_draft_preview.html",
        view=view,
        preview=preview,
        commission_rows=draft_commission_display_rows(db.session, draft),
        current_partner_tab="products",
    )


@partners.post("/products/drafts/<uuid:draft_id>/submit-saved")
@login_required
@limiter.limit("12 per hour")
def submit_saved_product_draft_route(draft_id):
    try:
        db.session.remove()
        with db.session.begin():
            draft = submit_saved_product_draft(
                db.session,
                user_id=current_user.id,
                draft_id=draft_id,
            )
        flash("Producto enviado a revisión. Aún no está publicado.", "success")
        return redirect(
            url_for(
                "partners.product_draft_preview",
                draft_id=draft.id,
                view="summary",
            )
        )
    except ProductDraftValidationError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        for message in dict.fromkeys(exc.errors.values()):
            flash(message, "error")
        return redirect(
            url_for(
                "partners.product_draft_preview",
                draft_id=draft_id,
                view="summary",
            )
        )
    except ProductDraftAccessError as exc:
        raise NotFound(str(exc)) from exc
    except ProductDraftStateError as exc:
        flash(str(exc), "warning")
        return redirect(
            url_for(
                "partners.product_draft_preview",
                draft_id=draft_id,
                view="summary",
            )
        )


@partners.get("/products/drafts/<uuid:draft_id>/barcode.svg")
@login_required
def product_draft_barcode(draft_id):
    try:
        draft = get_product_draft_for_user(db.session, user_id=current_user.id, draft_id=draft_id)
        if not draft.seller_sku:
            raise NotFound("Código de producto no encontrado.")
        svg = render_product_code128_svg(draft.seller_sku)
    except ProductDraftAccessError as exc:
        raise NotFound(str(exc)) from exc
    except BarcodeRenderError as exc:
        raise NotFound(str(exc)) from exc
    return Response(
        svg,
        mimetype="image/svg+xml",
        headers={"Cache-Control": "private, no-store"},
    )


@partners.post("/products/drafts/<uuid:draft_id>/files")
@login_required
@limiter.limit("30 per hour")
def upload_product_draft_file(draft_id):
    kind = ProductDraftFileKind.DOCUMENT if request.form.get("kind") == ProductDraftFileKind.DOCUMENT.value else ProductDraftFileKind.IMAGE
    uploaded_files = request.files.getlist("files") or request.files.getlist("file")
    uploaded_files = [item for item in uploaded_files if item and item.filename]
    if not uploaded_files:
        flash("Selecciona un archivo.", "error")
        if _wants_json():
            return {"ok": False, "errors": {"file": "Selecciona un archivo."}}, 422
        return redirect(url_for("partners.product_draft", draft_id=draft_id))
    if kind == ProductDraftFileKind.DOCUMENT and len(uploaded_files) > 1:
        message = "Carga un documento a la vez."
        flash(message, "error")
        if _wants_json():
            return {"ok": False, "errors": {"file": message}}, 422
        return redirect(url_for("partners.product_draft", draft_id=draft_id))
    storage_root = current_app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"]
    staged = []
    try:
        for uploaded in uploaded_files:
            staged.append(
                stage_product_draft_upload(
                    uploaded,
                    root=storage_root,
                    kind=kind,
                    max_bytes=current_app.config[
                        "PARTNER_PRODUCT_DOCUMENT_MAX_BYTES" if kind == ProductDraftFileKind.DOCUMENT else "PARTNER_PRODUCT_IMAGE_MAX_BYTES"
                    ],
                )
            )
        db.session.remove()
        with db.session.begin():
            file_records = attach_product_draft_files(
                db.session,
                user_id=current_user.id,
                draft_id=draft_id,
                staged_files=tuple(staged),
                kind=kind,
                document_type=request.form.get("document_type"),
                root=storage_root,
                max_images=current_app.config["PARTNER_PRODUCT_MAX_IMAGES"],
                variant_axis_key=request.form.get("variant_axis_key") or None,
                variant_value_key=request.form.get("variant_value_key") or None,
            )
        flash("Imágenes cargadas." if kind == ProductDraftFileKind.IMAGE else "Archivo cargado.", "success")
        if _wants_json():
            return _product_draft_gallery_payload(draft_id, file_ids=[str(item.id) for item in file_records])
    except ProductDraftValidationError as exc:
        db.session.rollback()
        for item in staged:
            item.temporary_path.unlink(missing_ok=True)
        message = next(iter(exc.errors.values()), str(exc))
        flash(message, "error")
        if _wants_json():
            return {"ok": False, "errors": exc.errors}, 422
    except ProductDraftAccessError as exc:
        for item in staged:
            item.temporary_path.unlink(missing_ok=True)
        raise NotFound(str(exc)) from exc
    except ProductDraftError as exc:
        for item in staged:
            item.temporary_path.unlink(missing_ok=True)
        flash(str(exc), "error")
    return redirect(url_for("partners.product_draft", draft_id=draft_id))


@partners.get("/products/drafts/<uuid:draft_id>/files/<uuid:file_id>")
@login_required
def product_draft_file(draft_id, file_id):
    try:
        draft = get_product_draft_for_user(db.session, user_id=current_user.id, draft_id=draft_id)
    except ProductDraftAccessError as exc:
        raise NotFound(str(exc)) from exc
    file_record = next((item for item in draft.files if item.id == file_id), None)
    if file_record is None or file_record.status != ProductDraftFileStatus.ACTIVE:
        raise NotFound("Archivo no encontrado.")
    path = private_file_path(current_app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"], file_record.storage_key)
    return send_file(path, mimetype=file_record.media_type, as_attachment=False, download_name=file_record.original_filename)


@partners.post("/products/drafts/<uuid:draft_id>/files/<uuid:file_id>/delete")
@login_required
def delete_product_draft_file_route(draft_id, file_id):
    try:
        db.session.remove()
        with db.session.begin():
            delete_product_draft_file(
                db.session,
                user_id=current_user.id,
                draft_id=draft_id,
                file_id=file_id,
                root=current_app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"],
            )
        flash("Archivo eliminado.", "success")
        if _wants_json():
            return _product_draft_gallery_payload(draft_id)
    except ProductDraftAccessError as exc:
        raise NotFound(str(exc)) from exc
    except ProductDraftError as exc:
        flash(str(exc), "error")
        if _wants_json():
            return {"ok": False, "errors": {"file": str(exc)}}, 422
    return redirect(url_for("partners.product_draft", draft_id=draft_id))


@partners.post("/products/drafts/<uuid:draft_id>/files/<uuid:file_id>/cover")
@login_required
def set_product_draft_cover_route(draft_id, file_id):
    try:
        db.session.remove()
        with db.session.begin():
            set_cover_image(db.session, user_id=current_user.id, draft_id=draft_id, file_id=file_id)
        flash("Portada actualizada.", "success")
        if _wants_json():
            return _product_draft_gallery_payload(draft_id)
    except ProductDraftAccessError as exc:
        raise NotFound(str(exc)) from exc
    except ProductDraftError as exc:
        flash(str(exc), "error")
        if _wants_json():
            return {"ok": False, "errors": {"images": str(exc)}}, 422
    return redirect(url_for("partners.product_draft", draft_id=draft_id))


@partners.post("/products/drafts/<uuid:draft_id>/files/<uuid:file_id>/assign")
@login_required
def assign_product_draft_image_route(draft_id, file_id):
    try:
        db.session.remove()
        with db.session.begin():
            assign_product_draft_image(
                db.session,
                user_id=current_user.id,
                draft_id=draft_id,
                file_id=file_id,
                variant_axis_key=request.form.get("variant_axis_key") or "",
                variant_value_key=request.form.get("variant_value_key") or "",
                max_images=current_app.config["PARTNER_PRODUCT_MAX_IMAGES"],
            )
        if _wants_json():
            return _product_draft_gallery_payload(draft_id)
        flash("Imagen asignada al color.", "success")
    except ProductDraftAccessError as exc:
        raise NotFound(str(exc)) from exc
    except ProductDraftError as exc:
        if _wants_json():
            return {"ok": False, "errors": {"images": str(exc)}}, 422
        flash(str(exc), "error")
    return redirect(url_for("partners.product_draft", draft_id=draft_id))


@partners.post("/products/drafts/<uuid:draft_id>/variant-media/delete")
@login_required
def delete_product_draft_variant_media_route(draft_id):
    try:
        db.session.remove()
        with db.session.begin():
            count = delete_product_draft_color_media(
                db.session,
                user_id=current_user.id,
                draft_id=draft_id,
                variant_axis_key=request.form.get("variant_axis_key") or "",
                variant_value_key=request.form.get("variant_value_key") or "",
                root=current_app.config["PARTNER_PRODUCT_DRAFT_UPLOAD_DIR"],
            )
        payload = _product_draft_gallery_payload(draft_id)
        payload["deleted_count"] = count
        return payload
    except ProductDraftAccessError as exc:
        raise NotFound(str(exc)) from exc
    except ProductDraftError as exc:
        return {"ok": False, "errors": {"images": str(exc)}}, 422


@partners.post("/products/drafts/<uuid:draft_id>/files/reorder")
@login_required
def reorder_product_draft_files_route(draft_id):
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("ordered_image_ids")
    if raw_ids is None:
        raw_ids = request.form.getlist("ordered_image_ids")
    if not isinstance(raw_ids, list):
        raw_ids = []
    ordered_ids = []
    for raw_id in raw_ids:
        try:
            ordered_ids.append(uuid.UUID(str(raw_id)))
        except (TypeError, ValueError):
            if _wants_json():
                return {"ok": False, "errors": {"images": "El orden de las imágenes no es válido."}}, 422
            flash("El orden de las imágenes no es válido.", "error")
            return redirect(url_for("partners.product_draft", draft_id=draft_id))
    try:
        db.session.remove()
        with db.session.begin():
            reorder_product_draft_images(
                db.session,
                user_id=current_user.id,
                draft_id=draft_id,
                ordered_image_ids=ordered_ids,
            )
        if _wants_json():
            return _product_draft_gallery_payload(draft_id)
        flash("Orden actualizado.", "success")
    except ProductDraftAccessError as exc:
        raise NotFound(str(exc)) from exc
    except ProductDraftError as exc:
        if _wants_json():
            return {"ok": False, "errors": {"images": str(exc)}}, 422
        flash(str(exc), "error")
    return redirect(url_for("partners.product_draft", draft_id=draft_id))


def _product_draft_gallery_payload(draft_id, *, file_ids=None):
    draft = get_product_draft_for_user(db.session, user_id=current_user.id, draft_id=draft_id)
    view = build_product_draft_view(draft)
    html = render_template("partners/_product_draft_gallery.html", view=view, errors={})
    return {
        "ok": True,
        "gallery_html": html,
        "count": len(view.image_files),
        "max_images": current_app.config["PARTNER_PRODUCT_MAX_IMAGES"],
        "file_ids": file_ids or [],
    }


def _category_json(categories):
    return [
        {
            "id": category.id,
            "name": category.name,
            "icon": category.icon,
            "subcategories": [
                {
                    "id": subcategory.id,
                    "name": subcategory.name,
                    "template_key": subcategory.template_key,
                }
                for subcategory in category.subcategories
            ],
        }
        for category in categories
    ]


def _wants_json() -> bool:
    return "application/json" in request.headers.get("Accept", "")


def _my_products_redirect():
    return redirect(
        url_for(
            "partners.my_products",
            q=(request.form.get("return_q") or "").strip()[:160] or None,
            status=(request.form.get("return_status") or "").strip()[:40] or None,
            category=(request.form.get("return_category") or "").strip()[:40] or None,
            page=(request.form.get("return_page") or "").strip()[:8] or None,
        )
    )


def _money_json(value) -> str:
    return f"{value:.2f}"


def _csv_safe(value) -> str:
    text = str(value or "")
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def _partner_order_detail_json(detail) -> dict:
    return {
        "seller_order_id": str(detail.seller_order_id),
        "seller_order_number": detail.seller_order_number,
        "order_number": detail.order_number,
        "buyer": {"name": detail.buyer_name, "phone": detail.buyer_phone},
        "payment_confirmed_label": detail.payment_confirmed_label,
        "ship_by_label": detail.ship_by_label,
        "delivery_window_label": detail.delivery_window_label,
        "is_dispatch_overdue": detail.is_dispatch_overdue,
        "buyer_delivery": {
            "method": detail.delivery_method_label,
            "name": detail.buyer_pickup_point_name,
            "address": detail.buyer_pickup_point_address,
        },
        "seller_dropoff": {
            "instruction": (
                "Entrega este paquete en cualquier punto Ecuvel Drop-off."
            )
        },
        "lines": [
            {
                "product_name": line.product_name,
                "sku": line.sku,
                "variant_name": line.variant_name,
                "quantity": line.quantity,
                "unit_price": _money_json(line.unit_price),
                "line_total": _money_json(line.line_total),
                "image_url": line.image_url,
            }
            for line in detail.lines
        ],
        "financials": {
            "currency": detail.currency,
            "product_subtotal": _money_json(detail.product_subtotal),
            "commission_total": _money_json(detail.commission_total),
            "seller_net_total": _money_json(detail.seller_net_total),
            "breakdown_available": detail.commission_breakdown_available,
            "commission_lines": [
                {
                    "product_name": line.product_name,
                    "category_name": line.category_name,
                    "rate": _money_json(line.rate) if line.rate is not None else None,
                    "amount": _money_json(line.amount) if line.amount is not None else None,
                }
                for line in detail.commission_lines
            ],
        },
        "workflow": {
            "stage": detail.workflow_stage,
            "label": detail.workflow_label,
            "tone": detail.workflow_tone,
            "can_prepare": detail.can_prepare,
        },
        "inbound_packages": [
            {
                "package_id": str(package.package_id),
                "package_code": package.package_code,
                "barcode": package.barcode,
                "status": package.status,
                "status_label": package.status_label,
                "label_url": package.label_url,
                "ready_url": (
                    f"/partners/orders/{detail.seller_order_id}/packages/"
                    f"{package.package_id}/ready"
                ),
                "can_print": package.can_print,
                "can_mark_ready": package.can_mark_ready,
                "ready_for_dropoff_label": package.ready_for_dropoff_label,
                "received_label": package.received_label,
                "received_location": package.received_location,
            }
            for package in detail.inbound_packages
        ],
        "timeline": [
            {
                "label": step.label,
                "is_complete": step.is_complete,
                "date_label": step.date_label,
            }
            for step in detail.timeline
        ],
        "decision": {
            "status": detail.decision_status,
            "label": detail.decision_label,
            "rejection_reason": detail.rejection_reason_label,
            "rejection_comment": detail.rejection_comment,
            "rejected_at_label": detail.rejected_at_label,
            "rejected_by_name": detail.rejected_by_name,
            "requires_refund_resolution": detail.requires_refund_resolution,
            "can_approve": detail.can_approve,
            "can_reject": detail.can_reject,
        },
        "logistics": {
            "status": detail.logistical_status,
            "label": detail.logistical_label,
        },
    }


def _partner_order_decision_json(result) -> dict:
    return {
        "order": {
            "seller_order_id": str(result.seller_order_id),
            "decision_status": result.decision_status.value,
            "decision_label": result.decision_label,
            "logistical_status": result.logistical_status.value,
            "logistical_label": result.logistical_label,
            "workflow_stage": result.workflow_stage,
            "workflow_label": result.workflow_label,
            "workflow_tone": result.workflow_tone,
            "replayed": result.replayed,
        },
        "metrics": {
            "pending": result.metrics.pending,
            "preparation": result.metrics.preparation,
            "logistics": result.metrics.logistics,
            "completed": result.metrics.completed,
            "rejected": result.metrics.rejected,
            "total": result.metrics.total,
        },
    }


def _partner_reviews_page_url(page: int) -> str:
    pairs: list[tuple[str, str]] = []
    for key, values in request.args.lists():
        if key == "page":
            continue
        pairs.extend((key, value) for value in values if value is not None)
    pairs.append(("page", str(max(1, page))))
    return f"{url_for('partners.partner_reviews')}?{urlencode(pairs)}"


def _partner_reviews_form_redirect(review_id: uuid.UUID):
    pairs: list[tuple[str, str]] = []
    query = " ".join((request.form.get("return_q") or "").split())[:160]
    if query:
        pairs.append(("q", query))
    for status in request.form.getlist("return_status"):
        if status in {"unanswered", "answered"}:
            pairs.append(("status", status))
    for rating in request.form.getlist("return_rating"):
        if rating in {"1", "2", "3", "4", "5"}:
            pairs.append(("rating", rating))
    sort = request.form.get("return_sort")
    if sort in {"newest", "oldest", "rating_high", "rating_low"}:
        pairs.append(("sort", sort))
    page = request.form.get("return_page")
    if page and page.isdigit():
        pairs.append(("page", page))
    query_string = urlencode(pairs)
    target = url_for("partners.partner_reviews")
    if query_string:
        target = f"{target}?{query_string}"
    return redirect(f"{target}#review-{review_id}")
