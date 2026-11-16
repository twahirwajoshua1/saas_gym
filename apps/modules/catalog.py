"""
The nine applications a gym can switch on, and what each one needs.

This is the structure of the product, so it lives in code and is reviewed like
code. Prices and plans are configurable data and will live in the database
(Step 7). The dependencies below come from the module dependency diagram in the
architecture plan.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AppModule:
    code: str
    name: str
    description: str
    requires: tuple[str, ...] = ()
    always_on: bool = False


GYM_OPERATIONS = "gym_operations"
PAYMENTS_FINANCE = "payments_finance"
ATTENDANCE_ACCESS = "attendance_access"
BUSINESS_INTELLIGENCE = "business_intelligence"
RETENTION_LOYALTY = "retention_loyalty"
SALES_GROWTH = "sales_growth"
TRAINING_CLASSES = "training_classes"
INVENTORY_MAINTENANCE = "inventory_maintenance"
CORPORATE_WELLNESS = "corporate_wellness"

CATALOG: tuple[AppModule, ...] = (
    AppModule(
        GYM_OPERATIONS,
        "Gym Operations",
        "Members, packages, staff and branches.",
        always_on=True,
    ),
    AppModule(
        PAYMENTS_FINANCE,
        "Payments and Finance",
        "Invoices, payments, receipts and the ledger.",
        requires=(GYM_OPERATIONS,),
    ),
    AppModule(
        ATTENDANCE_ACCESS,
        "Attendance and Access",
        "QR and manual check-in, with membership checks.",
        requires=(GYM_OPERATIONS,),
    ),
    AppModule(
        BUSINESS_INTELLIGENCE,
        "Business Intelligence",
        "Dashboards and reports across the applications the gym uses.",
        requires=(GYM_OPERATIONS,),
    ),
    AppModule(
        RETENTION_LOYALTY,
        "Retention and Loyalty",
        "Renewal reminders, inactive members, loyalty and referrals.",
        requires=(GYM_OPERATIONS, PAYMENTS_FINANCE, ATTENDANCE_ACCESS),
    ),
    AppModule(
        SALES_GROWTH,
        "Sales and Growth",
        "Leads, trials, the sales pipeline and campaigns.",
        requires=(GYM_OPERATIONS, PAYMENTS_FINANCE),
    ),
    AppModule(
        TRAINING_CLASSES,
        "Training and Classes",
        "Trainers, classes, bookings and fitness progress.",
        requires=(GYM_OPERATIONS, ATTENDANCE_ACCESS),
    ),
    AppModule(
        INVENTORY_MAINTENANCE,
        "Inventory and Maintenance",
        "Equipment, maintenance and stock. Product sales also use Payments.",
        requires=(GYM_OPERATIONS,),
    ),
    AppModule(
        CORPORATE_WELLNESS,
        "Corporate Wellness",
        "Employer accounts, employee benefits and corporate billing.",
        requires=(GYM_OPERATIONS, PAYMENTS_FINANCE, ATTENDANCE_ACCESS),
    ),
)

BY_CODE = {module.code: module for module in CATALOG}
