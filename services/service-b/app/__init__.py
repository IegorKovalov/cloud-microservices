"""service-b: storage / state microservice.

Owns a tiny in-memory key/value store and exposes it over a REST API.
Used by ``service-a`` as the canonical "downstream" service in the
fan-out concurrency demo.
"""
