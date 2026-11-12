# spacetaxi

- `docs/rules.md` is the source of truth for the game. Code implements it; when they disagree,
  fix the code, or change the rules doc first on purpose.
- Standard library only at runtime: `dependencies` in `pyproject.toml` stays empty.
- Tests use pytest (dev dependency): `uv run pytest`.
- Integers only in the simulation: positions, velocities, money and time are `int`; no floats.
- Package lives in `src/spacetaxi`, the console script `spacetaxi` is `spacetaxi.cli:main`
  (argparse subcommands).
