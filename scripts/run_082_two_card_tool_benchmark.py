"""Deprecated 0.8.2 benchmark; retained as a fail-closed tombstone.

The removed implementation read and copied ordinary desktop ``settings.json``
credentials.  That secret path is forbidden by the 0.8.3 repository policy.
"""

raise SystemExit(
    "DEPRECATED: this 0.8.2 benchmark is disabled because it could read/copy desktop "
    "settings.json secrets. Use an environment-only local harness outside CI and releases."
)
