"""Add foreign key constraints

Revision ID: 77c7447d8ee9
Revises: 342631e63bcc
Create Date: 2026-02-10 18:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '77c7447d8ee9'
down_revision: Union[str, None] = '342631e63bcc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add foreign keys to pharmacy_orders
    op.create_foreign_key(
        'fk_pharmacy_orders_customer',
        'pharmacy_orders', 'users',
        ['customer_id'], ['id'],
        ondelete='CASCADE'
    )
    op.create_foreign_key(
        'fk_pharmacy_orders_delivery',
        'pharmacy_orders', 'deliveries',
        ['delivery_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_pharmacy_orders_pharmacy',
        'pharmacy_orders', 'pharmacies',
        ['pharmacy_id'], ['id'],
        ondelete='CASCADE'
    )
    op.create_foreign_key(
        'fk_pharmacy_orders_prescription',
        'pharmacy_orders', 'prescriptions',
        ['prescription_id'], ['id'],
        ondelete='SET NULL'
    )

    # Add foreign keys to prescriptions
    op.create_foreign_key(
        'fk_prescriptions_patient',
        'prescriptions', 'users',
        ['patient_id'], ['id'],
        ondelete='CASCADE'
    )
    op.create_foreign_key(
        'fk_prescriptions_doctor',
        'prescriptions', 'doctors',
        ['doctor_id'], ['id'],
        ondelete='CASCADE'
    )
    op.create_foreign_key(
        'fk_prescriptions_consultation',
        'prescriptions', 'consultations',
        ['consultation_id'], ['id'],
        ondelete='CASCADE'
    )
    op.create_foreign_key(
        'fk_prescriptions_fulfilled_pharmacy',
        'prescriptions', 'pharmacies',
        ['fulfilled_pharmacy_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_prescriptions_fulfilled_order',
        'prescriptions', 'pharmacy_orders',
        ['fulfilled_order_id'], ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Drop foreign keys from prescriptions
    op.drop_constraint('fk_prescriptions_fulfilled_order', 'prescriptions', type_='foreignkey')
    op.drop_constraint('fk_prescriptions_fulfilled_pharmacy', 'prescriptions', type_='foreignkey')
    op.drop_constraint('fk_prescriptions_consultation', 'prescriptions', type_='foreignkey')
    op.drop_constraint('fk_prescriptions_doctor', 'prescriptions', type_='foreignkey')
    op.drop_constraint('fk_prescriptions_patient', 'prescriptions', type_='foreignkey')

    # Drop foreign keys from pharmacy_orders
    op.drop_constraint('fk_pharmacy_orders_prescription', 'pharmacy_orders', type_='foreignkey')
    op.drop_constraint('fk_pharmacy_orders_pharmacy', 'pharmacy_orders', type_='foreignkey')
    op.drop_constraint('fk_pharmacy_orders_delivery', 'pharmacy_orders', type_='foreignkey')
    op.drop_constraint('fk_pharmacy_orders_customer', 'pharmacy_orders', type_='foreignkey')