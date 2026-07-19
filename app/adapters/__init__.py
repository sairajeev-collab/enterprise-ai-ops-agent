"""Ports and adapters for external integrations.

Each subpackage owns one port (a ``Protocol`` in :mod:`app.adapters.base`) and
ships a real adapter plus a sandbox adapter. Selection happens in
:mod:`app.deps` from environment configuration.
"""
