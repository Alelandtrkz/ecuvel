"""add logistics tracking network and chain of custody

Revision ID: f4a5b6c7d8e9
Revises: e2f3a4b5c6d7
Create Date: 2026-08-09 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


package_status = postgresql.ENUM(
    "AT_POINT", "ASSIGNED", "IN_TRANSIT", "DEVIATED", "DELIVERED",
    name="logistics_package_status", create_type=False,
)
transfer_status = postgresql.ENUM(
    "ASSIGNED", "IN_TRANSIT", "RECEIVED", "DEVIATED", "CANCELLED",
    name="logistics_transfer_status", create_type=False,
)
event_type = postgresql.ENUM(
    "RECEIVED_AT_POINT",
    "TRANSFER_ASSIGNED",
    "TRANSFER_REASSIGNED",
    "PICKED_UP",
    "ARRIVAL_SCAN",
    "RECEIVED_AT_DESTINATION",
    "DEVIATION_DETECTED",
    "CORRECTIVE_TRANSFER_CREATED",
    "INCIDENT_REPORTED",
    name="logistics_tracking_event_type",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    package_status.create(bind, checkfirst=True)
    transfer_status.create(bind, checkfirst=True)
    event_type.create(bind, checkfirst=True)
    op.execute(
        "CREATE SEQUENCE logistics_transfer_number_seq "
        "START WITH 1 INCREMENT BY 1 NO MINVALUE MAXVALUE 99999999 CACHE 1"
    )

    op.create_table(
        "logistics_package_states",
        sa.Column("seller_inbound_package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", package_status, server_default="AT_POINT", nullable=False),
        sa.Column("current_warehouse_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("current_location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("custodian_warehouse_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("custodian_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expected_destination_warehouse_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deviated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(custodian_warehouse_id IS NULL) <> (custodian_user_id IS NULL)",
            name="logistics_state_exactly_one_custodian",
        ),
        sa.CheckConstraint(
            "current_location_id IS NULL OR current_warehouse_id IS NOT NULL",
            name="logistics_state_location_requires_warehouse",
        ),
        sa.CheckConstraint(
            "status != 'IN_TRANSIT' OR (current_warehouse_id IS NULL "
            "AND current_location_id IS NULL AND custodian_user_id IS NOT NULL)",
            name="logistics_state_in_transit_valid",
        ),
        sa.CheckConstraint(
            "status NOT IN ('AT_POINT', 'ASSIGNED', 'DEVIATED') OR "
            "(current_warehouse_id IS NOT NULL "
            "AND custodian_warehouse_id = current_warehouse_id)",
            name="logistics_state_at_point_custody_valid",
        ),
        sa.ForeignKeyConstraint(["seller_inbound_package_id"], ["seller_inbound_packages.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["current_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["current_location_id"], ["warehouse_locations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["custodian_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["custodian_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["expected_destination_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "seller_inbound_package_id", "status", "current_warehouse_id",
        "current_location_id", "custodian_warehouse_id", "custodian_user_id",
        "expected_destination_warehouse_id", "is_deviated", "last_event_at",
    ):
        op.create_index(
            op.f(f"ix_logistics_package_states_{column}"),
            "logistics_package_states",
            [column],
            unique=column == "seller_inbound_package_id",
        )
    op.create_index(
        "ix_logistics_states_status_last_event",
        "logistics_package_states",
        ["status", "last_event_at"],
    )
    op.create_index(
        "ix_logistics_states_deviated_last_event",
        "logistics_package_states",
        ["is_deviated", "last_event_at"],
    )

    op.create_table(
        "logistics_transfers",
        sa.Column(
            "transfer_code",
            sa.String(length=20),
            server_default=sa.text(
                "('TRF-' || lpad(nextval('logistics_transfer_number_seq'::regclass)::text, 8, '0'))"
            ),
            nullable=False,
        ),
        sa.Column("package_state_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin_warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination_warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", transfer_status, server_default="ASSIGNED", nullable=False),
        sa.Column("vehicle_code", sa.String(length=40), nullable=True),
        sa.Column("is_corrective", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("previous_transfer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eta_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("origin_warehouse_id <> destination_warehouse_id", name="logistics_transfer_distinct_points"),
        sa.CheckConstraint("vehicle_code IS NULL OR char_length(btrim(vehicle_code)) BETWEEN 1 AND 40", name="logistics_transfer_vehicle_code_valid"),
        sa.CheckConstraint("picked_up_at IS NULL OR picked_up_at >= assigned_at", name="logistics_transfer_pickup_after_assignment"),
        sa.CheckConstraint("received_at IS NULL OR (picked_up_at IS NOT NULL AND received_at >= picked_up_at)", name="logistics_transfer_receipt_after_pickup"),
        sa.CheckConstraint("eta_at IS NULL OR eta_at >= assigned_at", name="logistics_transfer_eta_after_assignment"),
        sa.ForeignKeyConstraint(["package_state_id"], ["logistics_package_states.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["origin_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["destination_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_transfer_id"], ["logistics_transfers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "transfer_code", "package_state_id", "origin_warehouse_id",
        "destination_warehouse_id", "assigned_user_id", "status",
        "previous_transfer_id", "assigned_at",
    ):
        op.create_index(
            op.f(f"ix_logistics_transfers_{column}"),
            "logistics_transfers",
            [column],
            unique=column == "transfer_code",
        )
    op.create_index(
        "uq_logistics_transfer_active_package",
        "logistics_transfers",
        ["package_state_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('ASSIGNED', 'IN_TRANSIT')"),
    )
    op.create_index(
        "ix_logistics_transfers_destination_status",
        "logistics_transfers",
        ["destination_warehouse_id", "status"],
    )

    op.create_table(
        "logistics_tracking_events",
        sa.Column("package_state_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transfer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_custodian_warehouse_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_custodian_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("new_custodian_warehouse_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("new_custodian_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=150), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("idempotency_key IS NULL OR char_length(btrim(idempotency_key)) BETWEEN 1 AND 150", name="logistics_event_idempotency_key_valid"),
        sa.CheckConstraint("location_id IS NULL OR warehouse_id IS NOT NULL", name="logistics_event_location_requires_warehouse"),
        sa.ForeignKeyConstraint(["package_state_id"], ["logistics_package_states.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transfer_id"], ["logistics_transfers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["location_id"], ["warehouse_locations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_custodian_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_custodian_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["new_custodian_warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["new_custodian_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "package_state_id", "transfer_id", "event_type", "occurred_at",
        "warehouse_id", "location_id", "actor_user_id", "idempotency_key",
    ):
        op.create_index(
            op.f(f"ix_logistics_tracking_events_{column}"),
            "logistics_tracking_events",
            [column],
            unique=column == "idempotency_key",
        )
    op.create_index(
        "ix_logistics_events_state_occurred",
        "logistics_tracking_events",
        ["package_state_id", "occurred_at"],
    )

    # Historical reception already carries reliable package, point, location,
    # timestamp and actor evidence, so it can be reconstructed without guessing.
    op.execute(
        """
        INSERT INTO logistics_package_states (
            id, seller_inbound_package_id, status, current_warehouse_id,
            current_location_id, custodian_warehouse_id, custodian_user_id,
            expected_destination_warehouse_id, is_deviated, last_event_at,
            created_at, updated_at
        )
        SELECT gen_random_uuid(), package.id, 'AT_POINT', location.warehouse_id,
               package.received_location_id, location.warehouse_id, NULL,
               NULL, false, package.received_at, package.received_at, now()
        FROM seller_inbound_packages AS package
        JOIN warehouse_locations AS location
          ON location.id = package.received_location_id
        WHERE package.status = 'RECEIVED_BY_ECUVEL'
          AND package.received_at IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM logistics_package_states AS state
              WHERE state.seller_inbound_package_id = package.id
          )
        """
    )
    op.execute(
        """
        INSERT INTO logistics_tracking_events (
            id, package_state_id, transfer_id, event_type, occurred_at,
            warehouse_id, location_id, previous_custodian_warehouse_id,
            previous_custodian_user_id, new_custodian_warehouse_id,
            new_custodian_user_id, actor_user_id, idempotency_key, notes,
            created_at, updated_at
        )
        SELECT gen_random_uuid(), state.id, NULL, 'RECEIVED_AT_POINT',
               package.received_at, state.current_warehouse_id,
               package.received_location_id, NULL, NULL,
               state.current_warehouse_id, NULL, package.received_by_user_id,
               'historical-reception:' || package.id::text,
               'Reconstruido desde la recepción persistida del paquete.',
               package.received_at, now()
        FROM logistics_package_states AS state
        JOIN seller_inbound_packages AS package
          ON package.id = state.seller_inbound_package_id
        WHERE NOT EXISTS (
            SELECT 1 FROM logistics_tracking_events AS event
            WHERE event.package_state_id = state.id
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_logistics_events_state_occurred", table_name="logistics_tracking_events")
    for column in reversed((
        "package_state_id", "transfer_id", "event_type", "occurred_at",
        "warehouse_id", "location_id", "actor_user_id", "idempotency_key",
    )):
        op.drop_index(op.f(f"ix_logistics_tracking_events_{column}"), table_name="logistics_tracking_events")
    op.drop_table("logistics_tracking_events")
    op.drop_index("ix_logistics_transfers_destination_status", table_name="logistics_transfers")
    op.drop_index("uq_logistics_transfer_active_package", table_name="logistics_transfers")
    for column in reversed((
        "transfer_code", "package_state_id", "origin_warehouse_id",
        "destination_warehouse_id", "assigned_user_id", "status",
        "previous_transfer_id", "assigned_at",
    )):
        op.drop_index(op.f(f"ix_logistics_transfers_{column}"), table_name="logistics_transfers")
    op.drop_table("logistics_transfers")
    op.drop_index("ix_logistics_states_deviated_last_event", table_name="logistics_package_states")
    op.drop_index("ix_logistics_states_status_last_event", table_name="logistics_package_states")
    for column in reversed((
        "seller_inbound_package_id", "status", "current_warehouse_id",
        "current_location_id", "custodian_warehouse_id", "custodian_user_id",
        "expected_destination_warehouse_id", "is_deviated", "last_event_at",
    )):
        op.drop_index(op.f(f"ix_logistics_package_states_{column}"), table_name="logistics_package_states")
    op.drop_table("logistics_package_states")
    op.execute("DROP SEQUENCE logistics_transfer_number_seq")
    event_type.drop(op.get_bind(), checkfirst=True)
    transfer_status.drop(op.get_bind(), checkfirst=True)
    package_status.drop(op.get_bind(), checkfirst=True)
