"""Shared persistence infrastructure: one SQLite file, one migration story.

Raw SQL by design (see docs/architecture.md D18 discussion): three small
tables, one writer process, and a schema debuggable with the sqlite3 CLI on
the Pi. The repository ports keep a Postgres exit open — new adapters, no
domain changes.
"""
