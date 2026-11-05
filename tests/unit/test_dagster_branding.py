"""Dagster UI branding: safe to repeat, reversible, and never blocks Dagster from starting."""

from orisun_iwadi.entrypoints.dagster import ui_branding as ub

PAGE = ('<html><head><title>Dagster</title>'
        '<link rel="icon" type="image/png" href="__PATH_PREFIX__/favicon.png"/>'
        '<link rel="icon" type="image/svg+xml" href="__PATH_PREFIX__/favicon.svg"/>'
        '</head><body></body></html>')


def _build(tmp_path, page=PAGE):
    (tmp_path / "index.html").write_text(page)
    return tmp_path


def test_apply_adds_the_stylesheet_logo_and_favicon_once(tmp_path):
    build = _build(tmp_path)
    assert ub.apply(build) and ub.apply(build)
    html = (build / "index.html").read_text()
    assert html.count(ub.MARKER) == 1
    assert "oncolake/dagster.css" in html and "oncolake/mark.svg" in html
    assert "favicon.png" not in html
    assert {p.name for p in (build / "oncolake").iterdir()} == {"dagster.css", "mark.svg"}


def test_remove_restores_the_original_page(tmp_path):
    build = _build(tmp_path)
    ub.apply(build)
    ub.remove(build)
    assert (build / "index.html").read_text() == PAGE
    assert not (build / "oncolake").exists()


def test_an_unrecognised_dagster_page_is_left_alone(tmp_path, capsys):
    build = _build(tmp_path, "<html>no head</html>")
    assert ub.apply(build) is False
    assert (build / "index.html").read_text() == "<html>no head</html>"
    assert "Dagster will show its own logo" in capsys.readouterr().err


def test_a_missing_dagster_page_is_a_warning_not_a_crash(tmp_path):
    assert ub.apply(tmp_path / "missing") is False


def test_the_command_always_exits_0(monkeypatch, tmp_path):
    monkeypatch.setattr(ub, "webapp_build_dir", lambda: tmp_path / "missing")
    assert ub.main([]) == 0
