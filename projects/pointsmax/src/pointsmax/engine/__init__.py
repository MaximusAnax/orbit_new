"""Pure domain logic for PointsMax.

Everything under ``pointsmax.engine`` is deterministic: no network, no
filesystem, no clock reads.  ``today`` and any randomness are explicit inputs.
"""
