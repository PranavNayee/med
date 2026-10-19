"""Prescription policy.

Under the Drugs and Cosmetics Act and the 1945 Rules, Schedule H, H1 and X
medicines may only be dispensed against a valid prescription, by or under
the supervision of a registered pharmacist. Schedule X and narcotics carry
additional record-keeping duties that an ordinary e-commerce flow does not
satisfy, so they are refused outright here rather than half-supported.

This module is the single place that decides those questions. Nothing else
in the codebase should branch on `drug_schedule` directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Schedule(str, Enum):
    OTC = "OTC"
    G = "G"      # carries a mandatory caution label
    H = "H"
    H1 = "H1"    # additional register; includes many antibiotics
    X = "X"      # psychotropics — dual copy, not dispensable online here
    NARCOTIC = "NARCOTIC"


#: Schedules we will not sell through this channel at all.
REFUSED_SCHEDULES = {Schedule.X, Schedule.NARCOTIC}

#: Schedules requiring an uploaded, pharmacist-verified prescription.
PRESCRIPTION_SCHEDULES = {Schedule.H, Schedule.H1}


@dataclass
class PolicyDecision:
    allowed: bool
    requires_prescription: bool
    reason_code: str = "OK"
    message: str = ""


def evaluate_item(drug_schedule: str, quantity: int,
                  max_rx_quantity: int = 3) -> PolicyDecision:
    """Decide whether a single line item may be sold, and on what condition."""
    try:
        schedule = Schedule(drug_schedule or "OTC")
    except ValueError:
        # An unknown schedule is treated as the most restrictive case, never
        # as OTC. Failing closed matters more than catalogue tidiness.
        return PolicyDecision(
            allowed=False, requires_prescription=True,
            reason_code="UNKNOWN_SCHEDULE",
            message="This medicine's regulatory schedule is not recorded, "
                    "so it cannot be dispensed.",
        )

    if schedule in REFUSED_SCHEDULES:
        return PolicyDecision(
            allowed=False, requires_prescription=True,
            reason_code="SCHEDULE_NOT_DISPENSABLE_ONLINE",
            message="This medicine cannot be supplied through online ordering. "
                    "Please visit a pharmacy in person with your prescription.",
        )

    if schedule in PRESCRIPTION_SCHEDULES:
        if quantity > max_rx_quantity:
            return PolicyDecision(
                allowed=False, requires_prescription=True,
                reason_code="RX_QUANTITY_EXCEEDED",
                message=f"A maximum of {max_rx_quantity} packs of a prescription "
                        f"medicine can be ordered at a time.",
            )
        return PolicyDecision(
            allowed=True, requires_prescription=True,
            reason_code="RX_REQUIRED",
            message="A valid doctor's prescription is required. Our pharmacist "
                    "will verify it before dispatch.",
        )

    return PolicyDecision(allowed=True, requires_prescription=False)


def evaluate_cart(items: list[tuple[str, int]],
                  max_rx_quantity: int = 3) -> tuple[bool, bool, list[PolicyDecision]]:
    """Evaluate a whole cart.

    Returns (cart_allowed, cart_requires_prescription, per-item decisions).
    One blocked item blocks checkout — partial fulfilment of a mixed cart is
    a support problem, not a feature.
    """
    decisions = [evaluate_item(sched, qty, max_rx_quantity) for sched, qty in items]
    allowed = all(d.allowed for d in decisions)
    requires_rx = any(d.requires_prescription for d in decisions)
    return allowed, requires_rx, decisions
