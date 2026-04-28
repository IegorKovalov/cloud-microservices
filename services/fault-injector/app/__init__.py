"""fault-injector: chaos engineering control plane.

Drives docker container lifecycle (kill/restart) on demand and exposes
self-contained ``/slow`` and ``/broken`` endpoints that simulate
latency and 500 errors. Used together with ``orchestration/recovery.py``
to demonstrate detect/recover loops end to end.
"""
