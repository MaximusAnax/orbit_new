"""Provider interfaces plus their offline (default) and live implementations.

Offline implementations are what tests and evals exercise.  Live
implementations activate only when credentials or optional dependencies are
present and are never imported by the offline path.
"""
