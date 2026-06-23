"""Allow ``python -m advtest_ext`` to invoke the CLI.

The ``advtest`` console-script entry point (declared in
``pyproject.toml`` under ``[tool.poetry.scripts]``) is the
recommended invocation; this module exists so module-form invocation
works identically without a fresh ``poetry install``.
"""

from advtest_ext.cli import main

if __name__ == "__main__":  # pragma: no cover
    main()
