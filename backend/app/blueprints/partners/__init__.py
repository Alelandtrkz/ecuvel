from __future__ import annotations

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session as browser_session,
    url_for,
)
from flask_login import current_user, login_required

from app.extensions import db, limiter
from app.models.enums import StoreOnboardingStatus
from app.services.partner_onboarding import (
    PartnerOnboardingError,
    PartnerOnboardingValidationError,
    STEPS,
    accept_contract,
    contract_pdf_bytes,
    get_or_create_onboarding,
    get_onboarding,
    request_contract_otp,
    save_step,
    stage_partner_document,
    submit_for_review,
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
from app.services.private_storage import private_file_path


partners = Blueprint("partners", __name__, url_prefix="/partners")


def _get_or_create_for_current_user():
    onboarding = get_onboarding(db.session, current_user.id)
    if onboarding is not None:
        return onboarding
    onboarding = get_or_create_onboarding(db.session, current_user.id)
    db.session.commit()
    return onboarding


def _render_main(onboarding, *, step: int | None = None, errors=None, form=None, status_code=200):
    form_values = form or {
        "store_name": onboarding.store_name or "",
        "legal_id_number": onboarding.legal_id_number or "",
        "province": onboarding.province or "",
        "city": onboarding.city or "",
        "address": onboarding.address or "",
        "whatsapp_or_nickname": onboarding.whatsapp_or_nickname or "",
        "bank_account_owner": onboarding.bank_account_owner or "",
        "bank_account_number": onboarding.bank_account_number or "",
        "bank_name": onboarding.bank_name or "",
        "bank_id_number": onboarding.bank_id_number or "",
        "bank_email": onboarding.bank_email or "",
    }
    return render_template(
        "partners/onboarding.html",
        onboarding=onboarding,
        steps=STEPS,
        step=step or onboarding.current_step,
        errors=errors or {},
        form=form_values,
        current_partner_tab="main",
    ), status_code


@partners.get("")
@login_required
def dashboard():
    onboarding = _get_or_create_for_current_user()
    if onboarding.status == StoreOnboardingStatus.COMPLETED:
        return redirect(url_for("partners.products"))
    if onboarding.status in {
        StoreOnboardingStatus.SUBMITTED,
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
    if step > onboarding.current_step:
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
    return render_template(
        "partners/status.html",
        onboarding=onboarding,
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
@login_required
def save_new_product_category():
    try:
        result = validate_category_selection(
            db.session,
            user_id=current_user.id,
            category_id=request.form.get("category_id"),
            subcategory_id=request.form.get("subcategory_id"),
        )
        save_product_category_selection(browser_session, result)
        return redirect(url_for("partners.new_product_details"))
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
