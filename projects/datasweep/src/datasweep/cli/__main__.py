"""``python -m datasweep.cli`` — the entry point EVALS.md M6 shells out to.

The determinism metric re-invokes the CLI in two subprocesses with different
``PYTHONHASHSEED`` values, which needs a module entry point that does not
depend on the console script being installed.
"""

from .app import main

if __name__ == "__main__":
    main()
