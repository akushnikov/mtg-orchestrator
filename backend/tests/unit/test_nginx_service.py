from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader


def _render(active_instances):
    templates_dir = Path(__file__).parent.parent.parent / "app" / "templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)))
    template = env.get_template("nginx.conf.j2")
    return template.render(
        panel_domain="p.x",
        active_instances=active_instances,
    )


def test_render_empty_instances():
    output = _render([])

    assert "resolver 127.0.0.11" in output
    assert '"p.x"  backend:8443' in output
    assert "default  mtg-default:3128" in output
    assert "{% for" not in output


def test_render_two_instances():
    output = _render(
        [
            SimpleNamespace(domain="ria.ru", slug="ria_ru", port=20000),
            SimpleNamespace(domain="api.max.ru", slug="api_max_ru", port=20001),
        ]
    )

    assert '"ria.ru"  mtg-ria_ru:20000' in output
    assert '"api.max.ru"  mtg-api_max_ru:20001' in output


def test_render_upstream_host_alias():
    output = _render(
        [
            SimpleNamespace(
                domain="ria.ru",
                slug="ria_ru",
                port=20000,
                upstream_host="mtgriaru",
            ),
        ]
    )

    assert '"ria.ru"  mtgriaru:20000' in output
    assert "mtg-ria_ru:20000" not in output


def test_render_stopped_instance_excluded_by_caller_contract():
    output = _render([])

    assert "mtg-ria_ru" not in output
    assert "default  mtg-default:3128" in output
