# tests/runtime

Unit and behaviour tests for the helpers that jobs execute, per the layout in
section 3 of `docs/gitlab-ci-agent-standard.md`.

| Directory | Runtime under test |
| --- | --- |
| `wiki/` | `runtime/wiki/` — the two-way wiki sync tools |
| `audit/` | `runtime/audit/` — repository set resolution and the Terraform audit scripts |
| `embed/` | `runtime/embed/generate.py` — the one generator that carries runtime into a template, and the gate that keeps the copy equal to its source |

The wiki tests load the code under test from `runtime/wiki/` by path, so they
must stay three directories below the repository root for `HERE` to resolve.

```bash
# GIT_CONFIG_GLOBAL is set per command, not exported: the runner's own checkout
# git reads it too and rejects /dev/null with "bad config line 1".
GIT_CONFIG_GLOBAL=/dev/null python3 -m pytest -q tests/runtime/
```
