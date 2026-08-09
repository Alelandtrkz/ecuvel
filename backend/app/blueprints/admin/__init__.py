from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template, request

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


admin = Blueprint("admin", __name__, url_prefix="/admin")


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
