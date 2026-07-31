"""Pure domain logic.

Nothing in this package touches the network, the filesystem or the clock: time
and randomness are explicit inputs (`now`/`as_of`, `seed`). Every module maps to
a numbered FR in ``docs/SCOPE.md``.
"""
