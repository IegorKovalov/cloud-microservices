"""service-a: data-processing worker with concurrency demos.

Receives a list of numbers from the API gateway, fans out per-item HTTP
calls to service-b in parallel via ``asyncio.gather``, optionally
delegates to the C++ worker for the heavier ``square_sum`` operation,
and aggregates the result. Also exposes alternate endpoints that
demonstrate an ``asyncio.Queue`` worker pool and a
``ThreadPoolExecutor`` fan-out.
"""
