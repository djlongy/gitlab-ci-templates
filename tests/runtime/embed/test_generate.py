"""The runtime a template carries must still be the runtime it names.

Section 12 puts substantial logic in runtime/<domain>/ with its own tests, and
rules out calling runtime/... at job time: `include:` imports YAML, not files.
Every component therefore carries a copy, produced by runtime/embed/generate.py.
Without a gate, runtime/ becomes documentation of what the jobs used to do.

The tests read the copy out of the PARSED YAML wherever they can, not out of the
template's raw text, because the block scalar and the heredoc are where a shell
script changes meaning: a literal newline or a terminator behaves differently
once every line has gained the block's indent. Where a region writes files, the
test runs the region's own shell and compares what landed on disk.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = REPO_ROOT / "templates"
REGION = re.compile(
    r"^(?P<indent>[ ]*)# BEGIN embed (?P<spec>[^\n]+)\n"
    r"(?P<body>.*?)^(?P=indent)# END embed\n",
    re.DOTALL | re.MULTILINE,
)


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "embed_generate", REPO_ROOT / "runtime" / "embed" / "generate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


embed = load_generator()


def template_paths() -> list[Path]:
    return sorted(TEMPLATES_DIR.glob("*/template.yml"))


def job_script(component: str) -> str:
    """Every command the jobs in this component run, as YAML yields them."""
    documents = list(
        yaml.safe_load_all((TEMPLATES_DIR / component / "template.yml").read_text())
    )
    commands: list[str] = []
    for job in documents[1].values():
        for key in ("before_script", "script", "after_script"):
            commands.extend(job.get(key, []))
    return "\n".join(commands) + "\n"


def parsed_regions(component: str) -> list[tuple[str, str]]:
    """(spec, body) for each embedded region, de-indented, from the job script."""
    out = []
    for match in REGION.finditer(job_script(component)):
        indent = match["indent"]
        body = "".join(
            (line[len(indent):] if line.startswith(indent) else line) + "\n"
            for line in match["body"].rstrip("\n").split("\n")
        )
        out.append((match["spec"].strip(), body))
    return out


# (component, spec) for every region in the repository. A component that starts
# or stops carrying runtime has to change this list, which is the point.
INVENTORY = sorted(
    (path.parent.name, spec)
    for path in template_paths()
    for spec, _ in parsed_regions(path.parent.name)
)
WRITING = [(c, s) for c, s in INVENTORY if not s.startswith("inline ")]


def destinations(spec: str) -> dict[str, str]:
    """Where a writing region puts each of its sources: source -> dest path."""
    mode, dest, *sources = spec.split()
    if mode == "file":
        return {sources[0]: dest}
    return {
        source: f"{dest}/{relative}"
        for source, relative in zip(sources, embed.relative_paths(sources))
    }


def run_region(body: str, tmp_path: Path) -> tuple[subprocess.CompletedProcess, dict]:
    """Execute a writing region with its destination variables bound into tmp."""
    environment = {
        "PATH": "/usr/bin:/bin",
        "CI_PROJECT_DIR": str(tmp_path),
        "CI_TPL_RUNTIME_DIR": str(tmp_path / "runtime"),
        "CI_TPL_HELPER": str(tmp_path / "helper.sh"),
        "CI_TPL_RUNTIME": str(tmp_path / "tfguard.sh"),
    }
    completed = subprocess.run(
        ["/bin/sh", "-c", body], env=environment, capture_output=True, text=True
    )
    return completed, environment


def expand(path: str, environment: dict) -> Path:
    """The destination the region's shell resolved, under the same bindings.

    Longest name first, so $CI_TPL_RUNTIME_DIR is not eaten by $CI_TPL_RUNTIME.
    """
    for name in sorted(environment, key=len, reverse=True):
        path = path.replace(f"${name}", environment[name])
    return Path(path)


# --------------------------------------------------------------------------
# The drift gate


def test_no_template_has_drifted_from_its_runtime_sources():
    assert embed.main(["--check"]) == 0, (
        "a template's embedded runtime no longer matches runtime/; "
        "run python3 runtime/embed/generate.py --write"
    )


@pytest.mark.parametrize(
    "mode,body",
    [
        ("inline runtime/demo/run.sh", "echo stale\n"),
        ("file $D/run.sh runtime/demo/run.sh", "cat > x <<'EOF'\nstale\nEOF\n"),
        ("dir $D runtime/demo/run.sh", "mkdir -p x\n"),
    ],
)
def test_the_check_notices_a_region_that_drifted(tmp_path, monkeypatch, mode, body):
    """Positive control, one per mode.

    A checker that always returned 0 would make the test above pass on any
    repository state, including one where every template had rotted.
    """
    templates = tmp_path / "templates" / "demo"
    templates.mkdir(parents=True)
    (tmp_path / "runtime" / "demo").mkdir(parents=True)
    (tmp_path / "runtime" / "demo" / "run.sh").write_text("echo current\n")
    template = templates / "template.yml"
    template.write_text(
        "job:\n  script:\n    - |\n"
        f"      # BEGIN embed {mode}\n"
        + "".join(f"      {line}\n" for line in body.splitlines())
        + "      # END embed\n"
    )
    monkeypatch.setattr(embed, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(embed, "TEMPLATES_DIR", tmp_path / "templates")

    assert embed.main(["--check"]) == 1
    assert embed.main(["--write"]) == 0
    assert "echo current" in template.read_text()
    assert embed.main(["--check"]) == 0


def test_the_inventory_of_embedded_regions_is_the_expected_one():
    """A component that starts carrying runtime must be added here deliberately."""
    assert INVENTORY == [
        ("ansible-lint", "dir $CI_TPL_RUNTIME_DIR runtime/scan/scan-gate.sh"),
        ("container-build-buildkit", "file $CI_TPL_HELPER runtime/registry/image-json.sh"),
        ("container-build-jib", "file $CI_TPL_HELPER runtime/registry/image-json.sh"),
        ("container-build-ko", "file $CI_TPL_HELPER runtime/registry/image-json.sh"),
        ("container-export-skopeo", "file $CI_TPL_RUNTIME runtime/mirror/transfer_manifest.py"),
        ("container-export-skopeo", "inline runtime/mirror/egress.sh"),
        ("container-export-skopeo", "inline runtime/mirror/registry.sh"),
        ("container-list-rke2", "file $CI_TPL_RUNTIME runtime/mirror/release_list.py"),
        ("container-list-rke2", "inline runtime/mirror/egress.sh"),
        ("container-mirror-skopeo", "inline runtime/mirror/egress.sh"),
        ("container-mirror-skopeo", "inline runtime/mirror/registry.sh"),
        ("container-promote-harbor",
         "dir $CI_TPL_RUNTIME_DIR runtime/httpjson.py runtime/promote/harbor.py"),
        ("container-sign-attest-cosign",
         "dir $CI_TPL_RUNTIME_DIR runtime/httpjson.py runtime/sign/cosign_attest.py "
         "runtime/sign/cosign_checksums.txt"),
        ("container-smoke-test", "file $CI_TPL_HELPER runtime/registry/image-json.sh"),
        ("docs-wiki-sync",
         "dir $CI_TPL_RUNTIME_DIR runtime/wiki/wiki-deploy.sh runtime/wiki/wiki-pull.py "
         "runtime/wiki/wiki-import.py runtime/wiki/wiki-sync.py runtime/wiki/docfilter.py "
         "runtime/wiki/requirements.txt"),
        ("helm-package-publish", "inline runtime/helm/package_publish.sh"),
        ("helm-package-publish", "inline runtime/kubernetes/toolchain.sh"),
        ("helm-validate", "inline runtime/helm/validate.sh"),
        ("helm-validate", "inline runtime/kubernetes/toolchain.sh"),
        ("kubernetes-validate", "inline runtime/kubernetes/toolchain.sh"),
        ("kubernetes-validate", "inline runtime/kubernetes/validate.sh"),
        ("quality-dependency-lockfiles",
         "dir $CI_PROJECT_DIR/.ci-tpl runtime/scan/lockfiles.sh"),
        ("quality-sonarqube", "dir $CI_PROJECT_DIR/.ci-tpl runtime/scan/sonar_gate.py"),
        ("security-filesystem-grype",
         "dir $CI_PROJECT_DIR/.ci-tpl runtime/scan/scan-gate.sh"),
        ("security-filesystem-trivy",
         "dir $CI_PROJECT_DIR/.ci-tpl runtime/scan/scan-gate.sh"),
        ("security-image-grype", "inline runtime/registry/image-json.sh"),
        ("security-image-grype", "inline runtime/scan/scan.sh"),
        ("security-image-trivy", "inline runtime/registry/image-json.sh"),
        ("security-image-trivy", "inline runtime/scan/scan.sh"),
        ("security-repository-audit",
         "dir $CI_TPL_RUNTIME_DIR runtime/audit/clone-repositories.sh "
         "runtime/audit/gitleaks-scan.sh runtime/audit/audit-secrets.sh "
         "runtime/audit/scan-state.sh"),
        ("security-sast-semgrep", "dir $CI_PROJECT_DIR/.ci-tpl runtime/scan/scan-gate.sh"),
        ("security-sbom-syft", "inline runtime/registry/image-json.sh"),
        ("security-sbom-syft", "inline runtime/scan/scan.sh"),
        ("security-sbom-upload-dtrack",
         "dir $CI_TPL_RUNTIME_DIR runtime/httpjson.py runtime/dtrack/dtrack.py"),
        ("security-secrets-gitleaks",
         "dir $CI_PROJECT_DIR/.ci-tpl runtime/scan/scan-gate.sh"),
        ("security-sync-vigil",
         "dir $CI_TPL_RUNTIME_DIR runtime/httpjson.py runtime/vigil/vigil.py"),
        ("security-verify-vigil",
         "dir $CI_TPL_RUNTIME_DIR runtime/httpjson.py runtime/vigil/vigil.py"),
        ("terraform-apply", "file $CI_TPL_RUNTIME runtime/terraform/tfguard.sh"),
        ("terraform-module-publish", "file $CI_TPL_RUNTIME runtime/terraform/tfguard.sh"),
        ("terraform-plan", "file $CI_TPL_RUNTIME runtime/terraform/tfguard.sh"),
    ]


# --------------------------------------------------------------------------
# What the job actually receives


@pytest.mark.parametrize("path", template_paths(), ids=lambda p: p.parent.name)
def test_every_inline_region_matches_its_source_byte_for_byte(path: Path):
    for source, script in embed.embedded_blocks(path).items():
        assert script == (REPO_ROOT / source).read_text(), (
            f"{path.parent.name} carries a different {source}"
        )


@pytest.mark.parametrize("component,spec", WRITING, ids=lambda v: str(v)[:60])
def test_a_writing_region_lands_its_sources_byte_for_byte(component, spec, tmp_path):
    """Run the region's own shell, then compare what is on disk.

    This is the end of the chain the components depend on: the heredoc, the
    block scalar and the indentation all have their turn at the payload before
    the job sees it.
    """
    body = next(b for s, b in parsed_regions(component) if s == spec)
    completed, environment = run_region(body, tmp_path)
    assert completed.returncode == 0, completed.stderr

    for source, destination in destinations(spec).items():
        landed = expand(destination, environment)
        assert landed.read_bytes() == (REPO_ROOT / source).read_bytes(), (
            f"{component} did not deliver {source} intact"
        )
        if source.endswith(".sh"):
            assert landed.stat().st_mode & 0o111, f"{destination} is not executable"


@pytest.mark.parametrize("component,spec", WRITING, ids=lambda v: str(v)[:60])
def test_a_written_shell_file_is_valid_after_yaml_and_the_heredoc(
    component, spec, tmp_path
):
    body = next(b for s, b in parsed_regions(component) if s == spec)
    _, environment = run_region(body, tmp_path)
    for source, destination in destinations(spec).items():
        if not source.endswith(".sh"):
            continue
        shebang = (REPO_ROOT / source).read_text().split("\n", 1)[0]
        shell = "/bin/bash" if shebang.endswith("bash") else "/bin/sh"
        result = subprocess.run(
            [shell, "-n", str(expand(destination, environment))],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("component,spec", WRITING, ids=lambda v: str(v)[:60])
def test_a_module_the_job_invokes_imports_and_runs(component, spec, tmp_path):
    """A Python module reaches the job as a module, with its imports resolvable."""
    body = next(b for s, b in parsed_regions(component) if s == spec)
    _, environment = run_region(body, tmp_path)
    script = job_script(component)
    for source, destination in destinations(spec).items():
        if not source.endswith(".py") or f'python3 "{destination}"' not in script:
            continue
        result = subprocess.run(
            ["python3", str(expand(destination, environment)), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("component,spec", WRITING, ids=lambda v: str(v)[:60])
def test_the_job_runs_something_the_region_wrote(component, spec):
    """A region that delivers runtime nothing invokes is dead weight."""
    script = job_script(component)
    outside = REGION.sub("", script)
    assert any(
        destination in outside for destination in destinations(spec).values()
    ), f"{component} writes {spec} but never runs any of it"


def test_a_failed_write_stops_the_job(tmp_path):
    """Positive control for the region's own error path.

    A runtime that did not land cannot be run, so the region exits non-zero
    rather than leaving the job to fail later with a confusing python3 error.
    """
    component, spec = next(
        (c, s) for c, s in WRITING if s.startswith("dir $CI_TPL_RUNTIME_DIR")
    )
    body = next(b for s, b in parsed_regions(component) if s == spec)
    blocked = tmp_path / "runtime"
    blocked.write_text("this is a file, not a directory")
    completed = subprocess.run(
        ["/bin/sh", "-c", body],
        env={"PATH": "/usr/bin:/bin", "CI_TPL_RUNTIME_DIR": str(blocked)},
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0


@pytest.mark.parametrize("path", template_paths(), ids=lambda p: p.parent.name)
def test_no_template_fetches_its_runtime_at_job_time(path: Path):
    """The defect this mechanism exists to remove.

    docs/wiki-sync.yml ran `git clone --branch "$WIKI_SYNC_REF"` (default main)
    of this project inside the job, so a consumer that pinned its include to a
    SHA still ran whatever was on main. security/terraform-audit.yml instead ran
    `bash scripts/audit-secrets.sh` from the consumer's checkout.

    The embedded regions are cut out first. This is about how the TEMPLATE gets
    its runtime; runtime/audit/clone-repositories.sh cloning the repositories it
    was asked to audit is the job doing its work, not a template fetching code.
    """
    commands = REGION.sub("", job_script(path.parent.name))
    assert "git clone" not in commands
    assert "WIKI_SYNC_REF" not in commands
    assert "scripts/" not in commands


# --------------------------------------------------------------------------
# What the generator refuses


def test_it_refuses_a_source_carrying_the_heredoc_delimiter(tmp_path):
    poisoned = tmp_path / "poisoned.py"
    poisoned.write_text(f"print('x')\n{embed.HEREDOC}\n")
    with pytest.raises(embed.EmbedError):
        embed.check_source(poisoned, poisoned.read_text())


def test_it_refuses_a_source_that_would_close_the_region(tmp_path):
    poisoned = tmp_path / "poisoned.sh"
    poisoned.write_text(f"echo x\n{embed.END}\n")
    with pytest.raises(embed.EmbedError):
        embed.check_source(poisoned, poisoned.read_text())


def test_it_refuses_trailing_whitespace(tmp_path):
    """yamllint rejects it once the file is embedded in a template."""
    untidy = tmp_path / "untidy.py"
    untidy.write_text("x = 1 \ny = 2\n")
    with pytest.raises(embed.EmbedError):
        embed.check_source(untidy, untidy.read_text())


@pytest.mark.parametrize(
    "dest", ['"$CI_TPL_RUNTIME"', "$(pwd)/x", "a b", "$X;rm -rf /", "`id`"]
)
def test_it_refuses_a_destination_that_is_not_a_plain_shell_word(dest):
    """Section 13.1: the region is shell, and no part of it may be eval bait."""
    with pytest.raises(embed.EmbedError):
        embed.check_dest(dest)


@pytest.mark.parametrize(
    "spec",
    [
        "inline",
        "inline a.sh b.sh",
        "file $D",
        "file $D a.sh b.sh",
        "dir $D",
        "sideways $D a.sh",
    ],
)
def test_it_refuses_a_marker_it_cannot_act_on(spec):
    with pytest.raises(embed.EmbedError):
        embed.render("", spec)


def test_it_refuses_a_begin_marker_with_no_matching_end():
    """A mis-indented END would otherwise stop the region being regenerated."""
    with pytest.raises(embed.EmbedError):
        embed.regions("  # BEGIN embed inline runtime/x.sh\n  echo hi\n# END embed\n")
