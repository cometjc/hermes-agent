"""Regression tests for gateway runtime PATH bootstrapping."""

import os


def test_gateway_prepends_home_bun_bin_to_path(tmp_path, monkeypatch):
    bun_bin = tmp_path / ".bun" / "bin"
    bun_bin.mkdir(parents=True)

    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run.Path, "home", lambda: tmp_path)

    gateway_run._prepend_path_dirs(gateway_run.Path.home() / ".bun" / "bin")

    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[0] == str(bun_bin.resolve())
    assert "/usr/bin" in parts
    assert "/bin" in parts
