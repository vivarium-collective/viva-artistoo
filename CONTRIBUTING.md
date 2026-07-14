# Contributing to pbg-artistoo

## Development setup

`uv` and **Node.js** (v18+) are required. Install uv with `brew install uv` or
`pip install uv`, and Node from https://nodejs.org.

    uv venv .venv
    source .venv/bin/activate
    uv pip install -e ".[dev]"
    # install the real Artistoo simulator for the Node bridge
    (cd pbg_artistoo/node_bridge && npm install)
    pytest

Tests that drive the real Artistoo CPM **skip** automatically when Node.js or
Artistoo is unavailable, so the suite stays green in a Python-only environment.

## Architecture

`pbg_artistoo/processes.py` is the process-bigraph `ArtistooProcess`. It spawns
`pbg_artistoo/node_bridge/artistoo_bridge.js` — a long-lived Node.js process
that drives the genuine Artistoo library — and speaks a newline-delimited JSON
protocol to it (`init` / `step` / `grid` / `quit`). Nothing reimplements the
Potts Hamiltonian; the JS library computes it.

## Releasing to PyPI

Tag a commit with `git tag v<VERSION>` and push the tag. The
`.github/workflows/release.yml` workflow publishes to PyPI automatically using
trusted publishing (no tokens needed after initial setup).

PyPI trusted publishing must be configured once per repo. See
https://docs.pypi.org/trusted-publishers/ and
[docs/conventions/distribution.md](https://github.com/vivarium-collective/pbg-superpowers/blob/main/docs/conventions/distribution.md).
