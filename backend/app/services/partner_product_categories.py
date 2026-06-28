from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Category, Store, StoreContractAcceptance, StoreMember, StoreOnboarding
from app.models.enums import (
    StoreContractAcceptanceStatus,
    StoreMemberRole,
    StoreOnboardingStatus,
    StoreStatus,
)


PARTNER_PRODUCT_DRAFT_SESSION_KEY = "partner_product_draft"


class PartnerProductCategoryError(Exception):
    pass


class PartnerProductAccessError(PartnerProductCategoryError):
    pass


class PartnerProductCategoryValidationError(PartnerProductCategoryError):
    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors = dict(errors or {})


@dataclass(frozen=True, slots=True)
class PartnerStoreAccess:
    store_id: uuid.UUID
    store_name: str


@dataclass(frozen=True, slots=True)
class PartnerSubcategoryView:
    id: str
    name: str
    code: str
    slug: str
    template_key: str
    parent_id: str


@dataclass(frozen=True, slots=True)
class PartnerMainCategoryView:
    id: str
    name: str
    code: str
    slug: str
    icon: str
    subcategories: tuple[PartnerSubcategoryView, ...]


@dataclass(frozen=True, slots=True)
class PartnerCategorySelectionPage:
    store: PartnerStoreAccess
    categories: tuple[PartnerMainCategoryView, ...]
    selected_category_id: str | None
    selected_subcategory_id: str | None
    selected_template_key: str | None


@dataclass(frozen=True, slots=True)
class PartnerCategorySelectionResult:
    store: PartnerStoreAccess
    category_id: uuid.UUID
    category_name: str
    subcategory_id: uuid.UUID
    subcategory_name: str
    template_key: str


_CATALOG_ROLES = {
    StoreMemberRole.OWNER,
    StoreMemberRole.ADMINISTRATOR,
}

_CATEGORY_ICONS = {
    "ELECTRONICS": "laptop",
    "FASHION": "shirt",
    "HOME_KITCHEN": "house",
    "BEAUTY_HEALTH": "flower-2",
    "AUTOMOTIVE": "car",
    "BABIES_KIDS": "baby",
}


def require_partner_catalog_store(session: Session, user_id: uuid.UUID) -> PartnerStoreAccess:
    row = session.execute(
        select(Store, StoreMember, StoreOnboarding, StoreContractAcceptance)
        .join(StoreMember, StoreMember.store_id == Store.id)
        .join(StoreOnboarding, StoreOnboarding.store_id == Store.id)
        .join(StoreContractAcceptance, StoreContractAcceptance.onboarding_id == StoreOnboarding.id)
        .where(
            StoreMember.user_id == user_id,
            StoreMember.is_active.is_(True),
            StoreMember.role.in_(_CATALOG_ROLES),
            Store.status == StoreStatus.ACTIVE,
            Store.is_verified.is_(True),
            StoreOnboarding.user_id == user_id,
            StoreOnboarding.status == StoreOnboardingStatus.COMPLETED,
            StoreContractAcceptance.status == StoreContractAcceptanceStatus.ACCEPTED,
        )
        .order_by(Store.created_at, Store.id)
        .limit(1)
    ).first()
    if row is None:
        raise PartnerProductAccessError("Su tienda todavía no está habilitada para publicar productos.")
    store, _member, _onboarding, _acceptance = row
    return PartnerStoreAccess(store_id=store.id, store_name=store.name)


def get_category_selection_page(
    session: Session,
    user_id: uuid.UUID,
    draft: Mapping[str, str] | None = None,
) -> PartnerCategorySelectionPage:
    store = require_partner_catalog_store(session, user_id)
    categories = list_main_categories(session)
    selected_category_id = _valid_draft_value(draft, "category_id")
    selected_subcategory_id = _valid_draft_value(draft, "subcategory_id")
    selected_template_key = _valid_draft_value(draft, "template_key")
    known_categories = {category.id for category in categories}
    if selected_category_id not in known_categories:
        selected_category_id = None
        selected_subcategory_id = None
        selected_template_key = None
    elif selected_subcategory_id:
        subcategory_ids = {
            subcategory.id
            for category in categories
            if category.id == selected_category_id
            for subcategory in category.subcategories
        }
        if selected_subcategory_id not in subcategory_ids:
            selected_subcategory_id = None
            selected_template_key = None
    return PartnerCategorySelectionPage(
        store=store,
        categories=categories,
        selected_category_id=selected_category_id,
        selected_subcategory_id=selected_subcategory_id,
        selected_template_key=selected_template_key,
    )


def list_main_categories(session: Session) -> tuple[PartnerMainCategoryView, ...]:
    rows = session.scalars(
        select(Category)
        .options(selectinload(Category.children))
        .where(Category.parent_id.is_(None), Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.name, Category.id)
    ).all()
    return tuple(_category_view(category) for category in rows)


def validate_category_selection(
    session: Session,
    *,
    user_id: uuid.UUID,
    category_id: str | None,
    subcategory_id: str | None,
) -> PartnerCategorySelectionResult:
    store = require_partner_catalog_store(session, user_id)
    errors: dict[str, str] = {}
    parsed_category_id = _parse_uuid(category_id)
    parsed_subcategory_id = _parse_uuid(subcategory_id)
    if parsed_category_id is None:
        errors["category_id"] = "Seleccione una categoría principal."
    if parsed_subcategory_id is None:
        errors["subcategory_id"] = "Seleccione una subcategoría."
    if errors:
        raise PartnerProductCategoryValidationError("Revisa la selección.", errors)

    category = session.get(Category, parsed_category_id)
    subcategory = session.get(Category, parsed_subcategory_id)
    if category is None or category.parent_id is not None or not category.is_active:
        errors["category_id"] = "La categoría ya no está disponible."
    if subcategory is None or subcategory.parent_id is None or not subcategory.is_active:
        errors["subcategory_id"] = "La subcategoría ya no está disponible."
    elif category is not None and subcategory.parent_id != category.id:
        errors["subcategory_id"] = "La subcategoría seleccionada no pertenece a la categoría."
    if errors:
        raise PartnerProductCategoryValidationError("Revisa la selección.", errors)
    return PartnerCategorySelectionResult(
        store=store,
        category_id=category.id,
        category_name=category.name,
        subcategory_id=subcategory.id,
        subcategory_name=subcategory.name,
        template_key=resolve_template_key(subcategory),
    )


def save_product_category_selection(browser_session, result: PartnerCategorySelectionResult) -> None:
    browser_session[PARTNER_PRODUCT_DRAFT_SESSION_KEY] = {
        "store_id": str(result.store.store_id),
        "category_id": str(result.category_id),
        "subcategory_id": str(result.subcategory_id),
        "template_key": result.template_key,
    }
    browser_session.modified = True


def get_saved_category_selection(session: Session, user_id: uuid.UUID, draft: Mapping[str, str] | None):
    if not draft:
        raise PartnerProductCategoryValidationError(
            "Selecciona una categoría antes de continuar.",
            {"category_id": "Seleccione una categoría principal."},
        )
    return validate_category_selection(
        session,
        user_id=user_id,
        category_id=draft.get("category_id"),
        subcategory_id=draft.get("subcategory_id"),
    )


def resolve_template_key(subcategory: Category) -> str:
    return subcategory.code.lower()


def _category_view(category: Category) -> PartnerMainCategoryView:
    children = sorted(
        (child for child in category.children if child.is_active),
        key=lambda child: (child.sort_order, child.name, str(child.id)),
    )
    return PartnerMainCategoryView(
        id=str(category.id),
        name=category.name,
        code=category.code,
        slug=category.slug,
        icon=_CATEGORY_ICONS.get(category.code, "tag"),
        subcategories=tuple(
            PartnerSubcategoryView(
                id=str(child.id),
                name=child.name,
                code=child.code,
                slug=child.slug,
                template_key=resolve_template_key(child),
                parent_id=str(category.id),
            )
            for child in children
        ),
    )


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _valid_draft_value(draft: Mapping[str, str] | None, key: str) -> str | None:
    value = (draft or {}).get(key)
    return str(value) if value else None
