"""Outbound integrations with existing administrative systems (local, signed, audited)."""

from .webhook import DeliveryResult, deliver, sign, verify

__all__ = ["DeliveryResult", "deliver", "sign", "verify"]
