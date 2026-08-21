from __future__ import annotations

import hashlib
import math
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload
from werkzeug.security import generate_password_hash

from app.models import (
    AdminAuditEvent,
    InventoryMovement,
    LogisticsPackageState,
    LogisticsTrackingEvent,
    LogisticsTransfer,
    Order,
    StaffAccessInvitation,
    StaffPointAssignment,
    StaffProfile,
    Store,
    StoreMember,
    User,
    UserMarketingConsent,
    Warehouse,
)
from app.models.enums import (
    LogisticsPackageStatus,
    LogisticsTransferStatus,
    MarketingConsentChannel,
    MarketingConsentStatus,
    OrderStatus,
    StaffEmploymentStatus,
    StaffIdentificationType,
    StaffOperationalStatus,
    StaffRole,
    UserStatus,
)
from app.models.user import normalize_email
from app.services.authentication import (
    PasswordPolicyError,
    normalize_full_name,
    public_user_code,
    validate_password,
)
from app.services.admin_permissions import ROLE_PERMISSIONS


PAGE_SIZE = 25
MAX_QUERY_LENGTH = 120
CLIENT_FILTERS = {
    "all", "active", "inactive", "email_verified", "unverified", "with_store",
    "marketing_email", "marketing_sms", "no_consent",
}
STAFF_ROLE_LABELS = {
    StaffRole.SUPER_ADMIN: "Administrador general",
    StaffRole.OPERATIONS_SUPERVISOR: "Supervisor de operaciones",
    StaffRole.POINT_OPERATOR: "Operador de Punto ECUVEL",
    StaffRole.DELIVERY: "Delivery",
    StaffRole.TRANSPORT_OPERATOR: "Personal de transporte",
    StaffRole.SUPPORT: "Soporte",
}
EMPLOYMENT_LABELS = {
    StaffEmploymentStatus.PENDING: "Pendiente",
    StaffEmploymentStatus.ACTIVE: "Activo",
    StaffEmploymentStatus.SUSPENDED: "Suspendido",
    StaffEmploymentStatus.INACTIVE: "Inactivo",
}
OPERATIONAL_LABELS = {
    StaffOperationalStatus.AVAILABLE: "Disponible",
    StaffOperationalStatus.ASSIGNED: "Asignado",
    StaffOperationalStatus.IN_ROUTE: "En ruta",
    StaffOperationalStatus.OFF_DUTY: "Fuera de turno",
}
PERMISSION_LABELS = {
    "admin.users.view": "Consultar clientes",
    "admin.users.manage": "Gestionar acceso de clientes",
    "admin.staff.view": "Consultar personal ECUVEL",
    "admin.staff.manage": "Gestionar personal ECUVEL",
    "scanner.use": "Usar escáner",
    "scanner.receive_package": "Recibir paquetes",
    "scanner.dispatch_package": "Despachar paquetes",
    "inventory.view_assigned_point": "Inventario del punto asignado",
    "fulfillment.view_assigned": "Consultar fulfillment asignado",
    "fulfillment.operate_assigned": "Operar fulfillment asignado",
    "orders.view_related": "Consultar pedidos relacionados",
    "products.moderate": "Moderar productos",
    "stores.moderate": "Moderar tiendas",
    "operations.supervise": "Supervisar operaciones",
}


class AdminUserError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ClientRow:
    user: User
    order_count: int
    email_consent: MarketingConsentStatus
    sms_consent: MarketingConsentStatus


@dataclass(frozen=True, slots=True)
class ClientsPage:
    rows: tuple[ClientRow, ...]
    query: str
    filter: str
    page: int
    pages: int
    total: int
    total_registered: int
    new_today: int
    email_verified_percent: int | None
    phone_verified_percent: int | None


@dataclass(frozen=True, slots=True)
class ClientDetail:
    user: User
    total_orders: int
    completed_orders: int
    in_progress_orders: int
    recent_orders: tuple[Order, ...]
    stores: tuple[Store, ...]
    consents: dict[MarketingConsentChannel, UserMarketingConsent]


@dataclass(frozen=True, slots=True)
class StaffRow:
    profile: StaffProfile
    assignment: StaffPointAssignment | None
    operational_status: StaffOperationalStatus
    access_status: str


@dataclass(frozen=True, slots=True)
class StaffPage:
    rows: tuple[StaffRow, ...]
    query: str
    role: str
    status: str
    operational: str
    warehouse_id: str
    page: int
    pages: int
    total: int
    active_count: int
    point_operator_count: int
    transport_count: int
    suspended_count: int


@dataclass(frozen=True, slots=True)
class StaffActivity:
    occurred_at: datetime
    label: str
    detail: str | None


@dataclass(frozen=True, slots=True)
class StaffDetail:
    profile: StaffProfile
    assignment: StaffPointAssignment | None
    operational_status: StaffOperationalStatus
    access_status: str
    permissions: tuple[str, ...]
    activity: tuple[StaffActivity, ...]
    has_active_custody: bool


@dataclass(frozen=True, slots=True)
class CreatedStaff:
    profile: StaffProfile
    invitation_token: str | None


def validate_ecuadorian_cedula(value: str) -> bool:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 10:
        return False
    province = int(digits[:2])
    if province < 1 or province > 24 or int(digits[2]) > 5:
        return False
    total = 0
    for index, character in enumerate(digits[:9]):
        item = int(character) * (2 if index % 2 == 0 else 1)
        total += item - 9 if item > 9 else item
    return (10 - total % 10) % 10 == int(digits[9])


def normalize_identification(kind: StaffIdentificationType, value: str) -> str:
    raw = (value or "").strip().upper()
    if kind == StaffIdentificationType.ECUADOR_CEDULA:
        normalized = re.sub(r"\D", "", raw)
        if not validate_ecuadorian_cedula(normalized):
            raise AdminUserError("La cédula ecuatoriana no supera la validación matemática.")
        return normalized
    normalized = re.sub(r"\s+", "", raw)
    if not 3 <= len(normalized) <= 40 or not re.fullmatch(r"[A-Z0-9.-]+", normalized):
        raise AdminUserError("Ingresa un número de identificación válido.")
    return normalized


def record_admin_audit(
    session: Session, *, actor_user_id, action: str, target_user_id=None,
    reason: str | None = None, metadata: dict | None = None,
) -> AdminAuditEvent:
    event = AdminAuditEvent(
        actor_user_id=actor_user_id, target_user_id=target_user_id,
        action=action, reason=(reason or "").strip()[:500] or None,
        metadata_json=metadata or None,
    )
    session.add(event)
    session.flush()
    return event


def _client_domain_condition():
    return or_(
        ~exists(select(StaffProfile.id).where(StaffProfile.user_id == User.id)),
        exists(select(Order.id).where(Order.buyer_id == User.id)),
        exists(select(StoreMember.id).where(StoreMember.user_id == User.id)),
    )


def _consent_status_subquery(channel: MarketingConsentChannel):
    return (
        select(UserMarketingConsent.status)
        .where(UserMarketingConsent.user_id == User.id, UserMarketingConsent.channel == channel)
        .correlate(User).scalar_subquery()
    )


def get_admin_clients_page(
    session: Session, *, query: str = "", filter_key: str = "all", page: int = 1,
) -> ClientsPage:
    query = " ".join((query or "").strip().split())[:MAX_QUERY_LENGTH]
    filter_key = filter_key if filter_key in CLIENT_FILTERS else "all"
    page = max(1, int(page or 1))
    conditions = [_client_domain_condition()]
    if query:
        public_match = re.fullmatch(r"U-(\d{1,8})", query, re.I)
        search_conditions = [
            User.full_name.ilike(f"%{query}%"), User.email_normalized.ilike(f"%{query.casefold()}%"),
            User.phone_normalized.ilike(f"%{query}%"), User.public_code.ilike(f"%{query}%"),
            exists(select(StoreMember.id).join(Store).where(
                StoreMember.user_id == User.id, Store.name.ilike(f"%{query}%")
            )),
        ]
        if public_match:
            search_conditions.append(User.registration_number == int(public_match.group(1)))
        conditions.append(or_(*search_conditions))
    granted_email = exists(select(UserMarketingConsent.id).where(
        UserMarketingConsent.user_id == User.id,
        UserMarketingConsent.channel == MarketingConsentChannel.EMAIL,
        UserMarketingConsent.status == MarketingConsentStatus.GRANTED,
    ))
    granted_sms = exists(select(UserMarketingConsent.id).where(
        UserMarketingConsent.user_id == User.id,
        UserMarketingConsent.channel == MarketingConsentChannel.SMS_WHATSAPP,
        UserMarketingConsent.status == MarketingConsentStatus.GRANTED,
    ))
    if filter_key == "active":
        conditions += [User.is_active.is_(True), User.status == UserStatus.ACTIVE]
    elif filter_key == "inactive":
        conditions.append(or_(User.is_active.is_(False), User.status.in_((UserStatus.SUSPENDED, UserStatus.BLOCKED))))
    elif filter_key == "email_verified":
        conditions.append(User.email_verified_at.is_not(None))
    elif filter_key == "unverified":
        conditions.append(or_(User.email_verified_at.is_(None), and_(User.phone.is_not(None), User.phone_verified_at.is_(None))))
    elif filter_key == "with_store":
        conditions.append(exists(select(StoreMember.id).where(StoreMember.user_id == User.id)))
    elif filter_key == "marketing_email":
        conditions.append(granted_email)
    elif filter_key == "marketing_sms":
        conditions.append(granted_sms)
    elif filter_key == "no_consent":
        conditions += [~granted_email, ~granted_sms]

    order_count = select(func.count(Order.id)).where(Order.buyer_id == User.id).correlate(User).scalar_subquery()
    base = select(User).where(*conditions)
    total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(page, pages)
    records = session.execute(
        select(User, order_count.label("order_count"),
               _consent_status_subquery(MarketingConsentChannel.EMAIL).label("email_consent"),
               _consent_status_subquery(MarketingConsentChannel.SMS_WHATSAPP).label("sms_consent"))
        .where(*conditions)
        .order_by(User.created_at.desc(), User.registration_number.desc())
        .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all()
    rows = tuple(ClientRow(
        user=item.User, order_count=int(item.order_count or 0),
        email_consent=item.email_consent or MarketingConsentStatus.UNKNOWN,
        sms_consent=item.sms_consent or MarketingConsentStatus.UNKNOWN,
    ) for item in records)
    domain = _client_domain_condition()
    registered = int(session.scalar(select(func.count(User.id)).where(domain)) or 0)
    today = datetime.now(timezone.utc).date()
    new_today = int(session.scalar(select(func.count(User.id)).where(domain, func.date(User.created_at) == today)) or 0)
    email_verified = int(session.scalar(select(func.count(User.id)).where(domain, User.email_verified_at.is_not(None))) or 0)
    users_with_phone = int(session.scalar(select(func.count(User.id)).where(domain, User.phone.is_not(None))) or 0)
    phone_verified = int(session.scalar(select(func.count(User.id)).where(domain, User.phone_verified_at.is_not(None))) or 0)
    return ClientsPage(
        rows=rows, query=query, filter=filter_key, page=page, pages=pages, total=total,
        total_registered=registered, new_today=new_today,
        email_verified_percent=round(email_verified * 100 / registered) if registered else None,
        phone_verified_percent=round(phone_verified * 100 / users_with_phone) if users_with_phone else None,
    )


def find_user_by_public_code(session: Session, value: str) -> User | None:
    match = re.fullmatch(r"U-(\d{1,8})", (value or "").strip(), re.I)
    if match:
        return session.scalar(select(User).where(User.registration_number == int(match.group(1))))
    try:
        return session.get(User, uuid.UUID(value))
    except (TypeError, ValueError):
        return None


def get_admin_client_detail(session: Session, identifier: str) -> ClientDetail | None:
    user = find_user_by_public_code(session, identifier)
    if user is None:
        return None
    counts = dict(session.execute(
        select(Order.status, func.count(Order.id)).where(Order.buyer_id == user.id).group_by(Order.status)
    ).all())
    recent = tuple(session.scalars(
        select(Order).where(Order.buyer_id == user.id).order_by(Order.created_at.desc()).limit(5)
    ).all())
    stores = tuple(session.scalars(
        select(Store).join(StoreMember).where(StoreMember.user_id == user.id).order_by(Store.name)
    ).all())
    consents = {item.channel: item for item in session.scalars(
        select(UserMarketingConsent).where(UserMarketingConsent.user_id == user.id)
    )}
    total = sum(int(value) for value in counts.values())
    completed = int(counts.get(OrderStatus.COMPLETED, 0))
    cancelled = int(counts.get(OrderStatus.CANCELLED, 0)) + int(counts.get(OrderStatus.EXPIRED, 0))
    return ClientDetail(
        user=user, total_orders=total, completed_orders=completed,
        in_progress_orders=max(0, total - completed - cancelled), recent_orders=recent,
        stores=stores, consents=consents,
    )


def _active_assignment(profile: StaffProfile) -> StaffPointAssignment | None:
    return next((item for item in profile.assignments if item.ends_at is None and item.is_primary), None)


def access_status(profile: StaffProfile, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    pending = any(i.accepted_at is None and i.revoked_at is None and i.expires_at > now for i in profile.invitations)
    if pending and not profile.user.password_hash:
        return "INVITATION_PENDING"
    if profile.user.is_active and profile.user.status == UserStatus.ACTIVE:
        return "ENABLED"
    return "DISABLED"


def _operational_statuses(session: Session, profiles: list[StaffProfile]) -> dict[uuid.UUID, StaffOperationalStatus]:
    result = {
        p.user_id: (StaffOperationalStatus.AVAILABLE if p.employment_status == StaffEmploymentStatus.ACTIVE else StaffOperationalStatus.OFF_DUTY)
        for p in profiles
    }
    user_ids = [p.user_id for p in profiles if p.employment_status == StaffEmploymentStatus.ACTIVE]
    if not user_ids:
        return result
    for user_id, transfer_status in session.execute(
        select(LogisticsTransfer.assigned_user_id, LogisticsTransfer.status)
        .where(LogisticsTransfer.assigned_user_id.in_(user_ids),
               LogisticsTransfer.status.in_((LogisticsTransferStatus.ASSIGNED, LogisticsTransferStatus.IN_TRANSIT)))
    ):
        if transfer_status == LogisticsTransferStatus.IN_TRANSIT:
            result[user_id] = StaffOperationalStatus.IN_ROUTE
        elif result[user_id] != StaffOperationalStatus.IN_ROUTE:
            result[user_id] = StaffOperationalStatus.ASSIGNED
    return result


def get_admin_staff_page(
    session: Session, *, query: str = "", role: str = "", status: str = "",
    operational: str = "", warehouse_id: str = "", page: int = 1,
) -> StaffPage:
    query = " ".join((query or "").strip().split())[:MAX_QUERY_LENGTH]
    role = role if role in {item.value for item in StaffRole} else ""
    status = status if status in {item.value for item in StaffEmploymentStatus} else ""
    operational = operational if operational in {item.value for item in StaffOperationalStatus} else ""
    try:
        point_id = uuid.UUID(warehouse_id) if warehouse_id else None
    except (TypeError, ValueError):
        point_id = None
        warehouse_id = ""
    page = max(1, int(page or 1))
    conditions = []
    if query:
        employee_match = re.fullmatch(r"EMP-(\d{1,8})", query, re.I)
        search = [
            User.full_name.ilike(f"%{query}%"),
            User.email_normalized.ilike(f"%{query.casefold()}%"),
            StaffProfile.identification_number_normalized.ilike(f"%{query.upper()}%"),
        ]
        if employee_match:
            search.append(StaffProfile.employee_number == int(employee_match.group(1)))
        conditions.append(or_(*search))
    if role:
        conditions.append(StaffProfile.role == StaffRole(role))
    if status:
        conditions.append(StaffProfile.employment_status == StaffEmploymentStatus(status))
    active_transfer = exists(select(LogisticsTransfer.id).where(
        LogisticsTransfer.assigned_user_id == StaffProfile.user_id,
        LogisticsTransfer.status.in_((LogisticsTransferStatus.ASSIGNED, LogisticsTransferStatus.IN_TRANSIT)),
    ))
    in_transit = exists(select(LogisticsTransfer.id).where(
        LogisticsTransfer.assigned_user_id == StaffProfile.user_id,
        LogisticsTransfer.status == LogisticsTransferStatus.IN_TRANSIT,
    ))
    assigned = exists(select(LogisticsTransfer.id).where(
        LogisticsTransfer.assigned_user_id == StaffProfile.user_id,
        LogisticsTransfer.status == LogisticsTransferStatus.ASSIGNED,
    ))
    if operational == StaffOperationalStatus.AVAILABLE.value:
        conditions += [StaffProfile.employment_status == StaffEmploymentStatus.ACTIVE, ~active_transfer]
    elif operational == StaffOperationalStatus.ASSIGNED.value:
        conditions += [StaffProfile.employment_status == StaffEmploymentStatus.ACTIVE, ~in_transit, assigned]
    elif operational == StaffOperationalStatus.IN_ROUTE.value:
        conditions += [StaffProfile.employment_status == StaffEmploymentStatus.ACTIVE, in_transit]
    elif operational == StaffOperationalStatus.OFF_DUTY.value:
        conditions.append(StaffProfile.employment_status != StaffEmploymentStatus.ACTIVE)
    if point_id:
        conditions.append(exists(
            select(StaffPointAssignment.id)
            .join(Warehouse, Warehouse.id == StaffPointAssignment.warehouse_id)
            .where(
                StaffPointAssignment.staff_profile_id == StaffProfile.id,
                StaffPointAssignment.warehouse_id == point_id,
                StaffPointAssignment.ends_at.is_(None),
                StaffPointAssignment.is_primary.is_(True),
                Warehouse.seller_store_id.is_(None),
                Warehouse.is_active.is_(True),
            )
        ))
    base = select(StaffProfile).join(User).where(*conditions)
    total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    pages = max(1, math.ceil(total / PAGE_SIZE)); page = min(page, pages)
    profiles = list(session.scalars(
        base.options(
            selectinload(StaffProfile.user),
            selectinload(StaffProfile.assignments).selectinload(StaffPointAssignment.warehouse),
            selectinload(StaffProfile.invitations),
        ).order_by(StaffProfile.created_at.desc(), StaffProfile.employee_number.desc())
        .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all())
    operational_states = _operational_statuses(session, profiles)
    rows = tuple(StaffRow(
        p, _active_assignment(p), operational_states[p.user_id], access_status(p)
    ) for p in profiles)
    grouped = dict(session.execute(
        select(StaffProfile.employment_status, func.count(StaffProfile.id)).group_by(StaffProfile.employment_status)
    ).all())
    point_count = int(session.scalar(select(func.count(StaffProfile.id)).where(
        StaffProfile.role == StaffRole.POINT_OPERATOR,
        StaffProfile.employment_status == StaffEmploymentStatus.ACTIVE,
    )) or 0)
    transport_count = int(session.scalar(select(func.count(StaffProfile.id)).where(
        StaffProfile.role.in_((StaffRole.DELIVERY, StaffRole.TRANSPORT_OPERATOR)),
        StaffProfile.employment_status == StaffEmploymentStatus.ACTIVE,
    )) or 0)
    return StaffPage(
        rows=rows, query=query, role=role, status=status, operational=operational,
        warehouse_id=str(point_id) if point_id else "", page=page, pages=pages, total=total,
        active_count=int(grouped.get(StaffEmploymentStatus.ACTIVE, 0)),
        point_operator_count=point_count, transport_count=transport_count,
        suspended_count=int(grouped.get(StaffEmploymentStatus.SUSPENDED, 0)),
    )


def find_staff_by_employee_code(session: Session, value: str, *, options: bool = True) -> StaffProfile | None:
    match = re.fullmatch(r"EMP-(\d{1,8})", (value or "").strip(), re.I)
    if not match:
        return None
    statement = select(StaffProfile).where(StaffProfile.employee_number == int(match.group(1)))
    if options:
        statement = statement.options(
            selectinload(StaffProfile.user),
            selectinload(StaffProfile.assignments).selectinload(StaffPointAssignment.warehouse),
            selectinload(StaffProfile.invitations),
        )
    return session.scalar(statement)


def get_admin_staff_detail(session: Session, employee_code: str) -> StaffDetail | None:
    profile = find_staff_by_employee_code(session, employee_code)
    if profile is None:
        return None
    operational = _operational_statuses(session, [profile])[profile.user_id]
    events: list[StaffActivity] = []
    for item in session.scalars(select(LogisticsTrackingEvent).where(
        LogisticsTrackingEvent.actor_user_id == profile.user_id
    ).order_by(LogisticsTrackingEvent.occurred_at.desc()).limit(8)):
        events.append(StaffActivity(item.occurred_at, item.event_type.value.replace("_", " ").title(), item.notes))
    for item in session.scalars(select(InventoryMovement).where(
        InventoryMovement.actor_user_id == profile.user_id
    ).order_by(InventoryMovement.created_at.desc()).limit(8)):
        events.append(StaffActivity(item.created_at, f"Inventario: {item.movement_type.value.replace('_', ' ').title()}", None))
    events.sort(key=lambda item: item.occurred_at, reverse=True)
    has_custody = bool(session.scalar(select(exists().where(
        LogisticsPackageState.custodian_user_id == profile.user_id,
        LogisticsPackageState.status != LogisticsPackageStatus.DELIVERED,
    ))))
    return StaffDetail(
        profile=profile, assignment=_active_assignment(profile), operational_status=operational,
        access_status=access_status(profile),
        permissions=tuple(PERMISSION_LABELS[p] for p in sorted(ROLE_PERMISSIONS.get(profile.role, ()))),
        activity=tuple(events[:8]), has_active_custody=has_custody,
    )


def operational_warehouses(session: Session) -> tuple[Warehouse, ...]:
    return tuple(session.scalars(select(Warehouse).where(
        Warehouse.seller_store_id.is_(None), Warehouse.is_active.is_(True)
    ).order_by(Warehouse.name)))


def create_staff_member(
    session: Session, *, actor_user_id, first_names: str, last_names: str, email: str,
    phone: str, identification_type: str, identification_number: str,
    nationality_code: str, role: str, employment_status: str,
    employment_started_at: date | None, warehouse_id: uuid.UUID | None,
    invitation_ttl_minutes: int, link_existing_user: bool = False,
) -> CreatedStaff:
    names = normalize_full_name(first_names); surnames = normalize_full_name(last_names)
    if len(names) < 2 or len(surnames) < 2:
        raise AdminUserError("Ingresa nombres y apellidos válidos.")
    normalized_email = normalize_email(email)
    if "@" not in normalized_email or len(normalized_email) > 254:
        raise AdminUserError("Ingresa un email corporativo válido.")
    try:
        id_type = StaffIdentificationType(identification_type)
        staff_role = StaffRole(role)
        employment = StaffEmploymentStatus(employment_status)
    except ValueError as exc:
        raise AdminUserError("La identificación, rol o estado no es válido.") from exc
    normalized_id = normalize_identification(id_type, identification_number)
    nationality = (nationality_code or "ECU").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", nationality):
        raise AdminUserError("La nacionalidad debe usar un código ISO de tres letras.")
    existing = session.scalar(select(User).where(User.email_normalized == normalized_email).with_for_update())
    if existing and not link_existing_user:
        raise AdminUserError("El email ya pertenece a una cuenta. Confirma expresamente que deseas vincularla.")
    if existing and existing.staff_profile:
        raise AdminUserError("La cuenta ya está vinculada a personal ECUVEL.")
    if existing:
        user = existing
        invitation_token = None
    else:
        user = User(
            public_code=public_user_code(), email=email.strip(), email_normalized=normalized_email,
            password_hash=None, full_name=f"{names} {surnames}", phone=phone.strip() or None,
            status=UserStatus.PENDING_VERIFICATION, is_active=False, is_ecuvel_staff=True,
        )
        session.add(user); session.flush()
        invitation_token = secrets.token_urlsafe(32)
    user.is_ecuvel_staff = True
    profile = StaffProfile(
        user_id=user.id, identification_type=id_type,
        identification_number_normalized=normalized_id, nationality_code=nationality,
        role=staff_role, employment_status=employment,
        employment_started_at=employment_started_at,
    )
    session.add(profile); session.flush()
    if warehouse_id is not None:
        warehouse = session.scalar(select(Warehouse).where(
            Warehouse.id == warehouse_id, Warehouse.seller_store_id.is_(None), Warehouse.is_active.is_(True)
        ))
        if warehouse is None:
            raise AdminUserError("El Punto ECUVEL seleccionado no es válido.")
        session.add(StaffPointAssignment(
            staff_profile_id=profile.id, warehouse_id=warehouse.id,
            starts_at=datetime.now(timezone.utc), is_primary=True,
            created_by_user_id=actor_user_id,
        ))
    elif staff_role == StaffRole.POINT_OPERATOR:
        raise AdminUserError("Un operador de punto requiere un Punto ECUVEL activo.")
    if invitation_token:
        session.add(StaffAccessInvitation(
            staff_profile_id=profile.id,
            token_hash=hashlib.sha256(invitation_token.encode()).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=invitation_ttl_minutes),
            created_by_user_id=actor_user_id,
        ))
    record_admin_audit(
        session, actor_user_id=actor_user_id, target_user_id=user.id,
        action="STAFF_CREATED", metadata={"employee_code": profile.employee_code, "role": staff_role.value},
    )
    session.flush()
    return CreatedStaff(profile=profile, invitation_token=invitation_token)


def create_staff_invitation(session: Session, *, profile: StaffProfile, actor_user_id, ttl_minutes: int) -> str:
    if profile.user.password_hash:
        raise AdminUserError(
            "La cuenta ya tiene credenciales. Usa el flujo de restablecimiento de contraseña."
        )
    if profile.employment_status in (
        StaffEmploymentStatus.SUSPENDED,
        StaffEmploymentStatus.INACTIVE,
    ):
        raise AdminUserError("El estado laboral no permite enviar una invitación de acceso.")
    now = datetime.now(timezone.utc)
    for invitation in session.scalars(select(StaffAccessInvitation).where(
        StaffAccessInvitation.staff_profile_id == profile.id,
        StaffAccessInvitation.accepted_at.is_(None), StaffAccessInvitation.revoked_at.is_(None),
    ).with_for_update()):
        invitation.revoked_at = now
    token = secrets.token_urlsafe(32)
    session.add(StaffAccessInvitation(
        staff_profile_id=profile.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=now + timedelta(minutes=ttl_minutes), created_by_user_id=actor_user_id,
    ))
    record_admin_audit(session, actor_user_id=actor_user_id, target_user_id=profile.user_id,
                       action="STAFF_INVITATION_SENT")
    session.flush()
    return token


def revoke_staff_invitations(
    session: Session, *, profile: StaffProfile, actor_user_id, reason: str,
) -> int:
    reason = " ".join((reason or "").strip().split())[:500]
    if not reason:
        raise AdminUserError("El motivo es obligatorio.")
    now = datetime.now(timezone.utc)
    invitations = list(session.scalars(select(StaffAccessInvitation).where(
        StaffAccessInvitation.staff_profile_id == profile.id,
        StaffAccessInvitation.accepted_at.is_(None),
        StaffAccessInvitation.revoked_at.is_(None),
    ).with_for_update()))
    if not invitations:
        raise AdminUserError("No existe una invitación pendiente que pueda revocarse.")
    for invitation in invitations:
        invitation.revoked_at = now
    record_admin_audit(
        session,
        actor_user_id=actor_user_id,
        target_user_id=profile.user_id,
        action="STAFF_INVITATION_REVOKED",
        reason=reason,
        metadata={"revoked_count": len(invitations)},
    )
    session.flush()
    return len(invitations)


def accept_staff_invitation(
    session: Session, *, token: str, password: str, confirmation: str, password_min_length: int,
) -> User:
    if password != confirmation:
        raise AdminUserError("Las contraseñas no coinciden.")
    try:
        validate_password(password, min_length=password_min_length)
    except PasswordPolicyError as exc:
        raise AdminUserError(str(exc)) from exc
    now = datetime.now(timezone.utc)
    invitation = session.scalar(select(StaffAccessInvitation).where(
        StaffAccessInvitation.token_hash == hashlib.sha256(token.strip().encode()).hexdigest()
    ).with_for_update())
    if invitation is None or invitation.accepted_at or invitation.revoked_at or invitation.expires_at <= now:
        raise AdminUserError("La invitación no es válida o ya caducó.")
    profile = session.get(StaffProfile, invitation.staff_profile_id, with_for_update=True)
    if profile is None or profile.employment_status in (StaffEmploymentStatus.SUSPENDED, StaffEmploymentStatus.INACTIVE):
        raise AdminUserError("La cuenta laboral no permite habilitar acceso.")
    user = session.get(User, profile.user_id, with_for_update=True)
    user.password_hash = generate_password_hash(password)
    user.is_active = True; user.is_ecuvel_staff = True; user.status = UserStatus.ACTIVE
    user.email_verified_at = user.email_verified_at or now
    invitation.accepted_at = now
    record_admin_audit(session, actor_user_id=user.id, target_user_id=user.id, action="STAFF_INVITATION_ACCEPTED")
    session.flush()
    return user


def set_user_suspension(session: Session, *, user: User, actor_user_id, suspend: bool, reason: str) -> None:
    reason = " ".join((reason or "").strip().split())[:500]
    if not reason:
        raise AdminUserError("El motivo es obligatorio.")
    if user.id == actor_user_id and suspend:
        raise AdminUserError("No puedes suspender tu propia cuenta administrativa.")
    user.is_active = not suspend
    user.status = UserStatus.SUSPENDED if suspend else UserStatus.ACTIVE
    record_admin_audit(session, actor_user_id=actor_user_id, target_user_id=user.id,
                       action="USER_SUSPENDED" if suspend else "USER_REACTIVATED", reason=reason)
    session.flush()


def set_staff_access(session: Session, *, profile: StaffProfile, actor_user_id, enable: bool, reason: str) -> None:
    reason = " ".join((reason or "").strip().split())[:500]
    if not reason:
        raise AdminUserError("El motivo es obligatorio.")
    if enable and profile.employment_status in (StaffEmploymentStatus.SUSPENDED, StaffEmploymentStatus.INACTIVE):
        raise AdminUserError("No se puede habilitar acceso a personal suspendido o inactivo.")
    user = session.get(User, profile.user_id, with_for_update=True)
    user.is_active = enable
    user.status = UserStatus.ACTIVE if enable else UserStatus.SUSPENDED
    record_admin_audit(session, actor_user_id=actor_user_id, target_user_id=user.id,
                       action="STAFF_ACCESS_ENABLED" if enable else "STAFF_ACCESS_DISABLED", reason=reason)
    session.flush()


def update_staff_profile(
    session: Session, *, profile: StaffProfile, actor_user_id, role: str,
    employment_status: str, phone: str, warehouse_id: uuid.UUID | None, reason: str,
) -> None:
    try:
        new_role = StaffRole(role); new_status = StaffEmploymentStatus(employment_status)
    except ValueError as exc:
        raise AdminUserError("El rol o estado laboral no es válido.") from exc
    reason = " ".join((reason or "").strip().split())[:500]
    old_status = profile.employment_status
    if new_status != old_status and not reason:
        raise AdminUserError("Indica el motivo del cambio laboral.")
    if new_status in (StaffEmploymentStatus.SUSPENDED, StaffEmploymentStatus.INACTIVE):
        has_custody = session.scalar(select(exists().where(
            LogisticsPackageState.custodian_user_id == profile.user_id,
            LogisticsPackageState.status != LogisticsPackageStatus.DELIVERED,
        )))
        if has_custody:
            raise AdminUserError("El empleado conserva paquetes bajo custodia. Reasigna la custodia antes de suspenderlo.")
        user = session.get(User, profile.user_id, with_for_update=True)
        user.is_active = False; user.status = UserStatus.SUSPENDED
    profile.role = new_role; profile.employment_status = new_status
    profile.last_employment_reason = reason or profile.last_employment_reason
    profile.user.phone = phone.strip() or None
    current = _active_assignment(profile)
    if warehouse_id != (current.warehouse_id if current else None):
        if current:
            current.ends_at = datetime.now(timezone.utc)
        if warehouse_id:
            warehouse = session.scalar(select(Warehouse).where(
                Warehouse.id == warehouse_id, Warehouse.seller_store_id.is_(None), Warehouse.is_active.is_(True)
            ))
            if warehouse is None:
                raise AdminUserError("El Punto ECUVEL seleccionado no es válido.")
            session.add(StaffPointAssignment(
                staff_profile_id=profile.id, warehouse_id=warehouse.id,
                starts_at=datetime.now(timezone.utc), is_primary=True, created_by_user_id=actor_user_id,
            ))
        elif new_role == StaffRole.POINT_OPERATOR:
            raise AdminUserError("Un operador de punto requiere asignación.")
    record_admin_audit(session, actor_user_id=actor_user_id, target_user_id=profile.user_id,
                       action="STAFF_UPDATED", reason=reason,
                       metadata={"role": new_role.value, "employment_status": new_status.value})
    session.flush()
