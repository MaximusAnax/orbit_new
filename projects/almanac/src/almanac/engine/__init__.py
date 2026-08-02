"""Pure domain logic for Almanac.

Every module in this package is a deterministic function of its inputs: no
network, no filesystem, no clock reads, no ``random``.  Time enters as a
``datetime.date`` parameter and randomness as an integer ``seed`` threaded
through SHA-256 keyed jitter (SCOPE.md D11, FR-17).
"""
