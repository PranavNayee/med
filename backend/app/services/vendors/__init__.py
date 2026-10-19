"""Vendor adapter registry.

Each adapter is a class or callable registered by name.  The
`get()` function returns the adapter for a given vendor name, or
raises KeyError if it is not configured.
"""
from __future__ import annotations

from .base import VendorAdapter, VendorError

def register(name: str, adapter: VendorAdapter) -> None:
    _adapters[name.lower()] = adapter

_adapters: dict[str, VendorAdapter] = {}

# Register default adapters
from .shiprocket import ShiprocketProvider
register("shiprocket", ShiprocketProvider)


def get(name: str) -> VendorAdapter:
    adapter = _adapters.get(name.lower())
    if adapter is None:
        raise KeyError(f"Vendor '{name}' is not registered")
    return adapter


__all__ = ["register", "get", "VendorAdapter", "VendorError"]