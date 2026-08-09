from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user

from app.extensions import db, limiter
from app.services.admin_access import ecuvel_staff_required
from app.services.admin_navigation import (
    ADMIN_NAVIGATION,
    ADMIN_SECONDARY_NAVIGATION,
    find_admin_navigation_item,
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
from app.services.payment_proofs import (
    PaymentProofServiceError,
    review_payment_proof,
)
from app.services.private_storage import PrivateStorageError, verify_private_file


admin = Blueprint("admin", __name__, url_prefix="/admin")


@admin.app_template_filter("ecuador_datetime")
def ecuador_datetime(value) -> str:
    return format_ecuador_datetime(value)


def _shell_context(section: str) -> dict:
    return {
        "admin_navigation": ADMIN_NAVIGATION,
        "admin_secondary_navigation": ADMIN_SECONDARY_NAVIGATION,
        "current_admin_section": section,
    }


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
