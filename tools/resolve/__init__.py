"""Resolve this repository's components and compositions the way GitLab does.

Two consumers depend on these modules and must not drift apart:

- `tests/pipelines/` renders a component or composition and posts the result to
  the CI Lint API, which is what earns the `gitlab-linted` evidence label.
- `tools/ci-local.py` renders the same thing and runs one job's script in the
  job's own image.

They lived under `tests/pipelines/` while the lint harness was the only caller.
A tool importing out of a test directory is the wrong way round, so they moved
here; the substitution rules they implement are still pinned against the live
API by the probes in `tests/pipelines/test_container_components.py`, which is
what makes a local render trustworthy rather than a second opinion.

Import them as a package:

    from tools.resolve import composition, render_component
"""

from . import composition, render_component  # noqa: F401
