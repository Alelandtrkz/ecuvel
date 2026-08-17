from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AdminNavigationItem:
    key: str
    label: str
    icon: str
    endpoint: str
    implemented: bool = False


@dataclass(frozen=True, slots=True)
class AdminNavigationGroup:
    label: str
    items: tuple[AdminNavigationItem, ...]


ADMIN_NAVIGATION = (
    AdminNavigationGroup(
        "Operaciones",
        (
            AdminNavigationItem(
                "operations", "Centro de operaciones", "layout-dashboard",
                "admin.operations", True,
            ),
            AdminNavigationItem(
                "orders", "Pedidos", "shopping-cart", "admin.orders", True,
            ),
            AdminNavigationItem(
                "fulfillment", "Fulfillment", "truck", "admin.fulfillment", True,
            ),
            AdminNavigationItem("scanner", "Escáner", "scan-line", "admin.scanner", True),
            AdminNavigationItem(
                "inventory", "Inventario", "warehouse", "admin.inventory", True
            ),
        ),
    ),
    AdminNavigationGroup(
        "Marketplace",
        (
            AdminNavigationItem("products", "Productos", "shapes", "admin.products", True),
            AdminNavigationItem("stores", "Tiendas", "store", "admin.stores", True),
            AdminNavigationItem("users", "Usuarios", "users", "admin.module_placeholder"),
            AdminNavigationItem("reviews", "Reseñas", "message-square-text", "admin.module_placeholder"),
        ),
    ),
    AdminNavigationGroup(
        "Finanzas",
        (
            AdminNavigationItem("payments", "Pagos", "banknote", "admin.module_placeholder"),
            AdminNavigationItem("payouts", "Liquidaciones", "wallet-cards", "admin.module_placeholder"),
        ),
    ),
    AdminNavigationGroup(
        "Control",
        (
            AdminNavigationItem("incidents", "Incidencias", "triangle-alert", "admin.module_placeholder"),
            AdminNavigationItem("audit", "Auditoría", "history", "admin.module_placeholder"),
        ),
    ),
)

ADMIN_SECONDARY_NAVIGATION = (
    AdminNavigationItem("settings", "Configuración", "settings", "admin.module_placeholder"),
    AdminNavigationItem("support", "Soporte", "circle-help", "admin.module_placeholder"),
)


def find_admin_navigation_item(key: str) -> AdminNavigationItem | None:
    for group in ADMIN_NAVIGATION:
        for item in group.items:
            if item.key == key:
                return item
    for item in ADMIN_SECONDARY_NAVIGATION:
        if item.key == key:
            return item
    return None
