"""Single-brand order ledger ported from the teammate's tested UC-4 prototype.

SQLite transactions preserve stock and financial-entry integrity locally.
Do not use this as a multi-brand or multi-node order store.
"""
