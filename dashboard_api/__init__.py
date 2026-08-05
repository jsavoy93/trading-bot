"""Read-only engineering dashboard package.

This package is intentionally distinct from the legacy top-level ``dashboard.py``
trading dashboard module.  It must remain read-only and must not import trading,
brokerage, Alpaca, or trading database modules.
"""

from dashboard_api.engineering_read_model import (
    DashboardSnapshot,
    EngineeringDashboardReadModel,
    EngineeringHealthSummary,
    TaskStatusSummary,
    TestingSummary,
)
from dashboard_api.providers import (
    EngineeringDashboardProviderConfig,
    create_engineering_dashboard_provider,
)

__all__ = [
    "DashboardSnapshot",
    "EngineeringDashboardProviderConfig",
    "EngineeringDashboardReadModel",
    "EngineeringHealthSummary",
    "TaskStatusSummary",
    "TestingSummary",
    "create_engineering_dashboard_provider",
]
