"""Pure domain logic: deterministic, no network, no filesystem, no clock reads.

Time and randomness are explicit inputs (`as_of`, `published_at`, `observed_at`,
`placebo_seed`).  Every module here is importable without touching I/O.
"""

from . import (
    backtest,
    brief,
    cluster,
    digest,
    extract,
    frame,
    link,
    normalize,
    pipeline,
    revise,
    score,
)

__all__ = [
    "backtest",
    "brief",
    "cluster",
    "digest",
    "extract",
    "frame",
    "link",
    "normalize",
    "pipeline",
    "revise",
    "score",
]
