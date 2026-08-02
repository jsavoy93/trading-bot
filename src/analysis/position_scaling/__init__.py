"""
Position Scaling Module

Scale in/out for positions.
"""
from .position_scaling import (
    EntryTranche,
    ExitTranche,
    calculate_entry_tranches,
    calculate_exit_tranches,
    check_tranche_triggers,
    update_trailing_stops,
    check_exit_triggers,
    cancel_pending_entry_tranches,
    save_position_tranches,
    update_tranche_status,
)

__all__ = [
    'EntryTranche',
    'ExitTranche',
    'calculate_entry_tranches',
    'calculate_exit_tranches',
    'check_tranche_triggers',
    'update_trailing_stops',
    'check_exit_triggers',
    'cancel_pending_entry_tranches',
    'save_position_tranches',
    'update_tranche_status',
]
