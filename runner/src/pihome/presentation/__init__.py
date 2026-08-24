"""Presentation layer: the D16 HTTP API and the D17 server-rendered board.

One FastAPI process serves both — one surface, one auth path, one thing to
restart. Commands come in here; state goes out via telemetry.
"""
