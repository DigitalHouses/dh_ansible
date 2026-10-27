"""Offline acceptance of the client-friendly media VM landing page."""
from pathlib import Path
from xml.etree import ElementTree as ET
import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/media_guest_setup"
PAGE = ROLE / "templates/media-portal-index.html.j2"
BONJOUR = ROLE / "templates/media-smb-avahi.service.j2"
PORTAL_TASKS = ROLE / "tasks/discovery_portal.yml"

APPS = {
    "jellyfin": ("Jellyfin", ":8096/"),
    "filestash": ("Filestash", ":8334/"),
    "sambee": ("Sambee", ":8000/"),
    "radarr": ("Radarr", ":7878/"),
    "sonarr": ("Sonarr", ":8989/"),
    "prowlarr": ("Prowlarr", ":9696/"),
    "qbittorrent": ("qBittorrent", ":8080/"),
}


def render_html(applications, admin=False):
    env = Environment(autoescape=True, undefined=StrictUndefined)
    env.filters["bool"] = bool
    vm = {
        "name": "mediaserver-demo",
        "network": {"ipv4": "192.0.2.238/24"},
        "discovery": {
            "friendly_name": "Домашний медиасервер",
            "netbios_name": "MEDIA-DEMO",
        },
        "applications": applications,
        "portal": {
            "show_admin_links": admin,
            "links": [{"url": "http://192.0.2.33:8123",
                       "label": "Home Assistant",
                       "description": "Управление домом"}],
            "admin_links": [{"url": "https://192.0.2.30:8006",
                             "label": "Proxmox VE",
                             "description": "Система виртуализации"}],
        },
    }
    return env.from_string(PAGE.read_text()).render(media_vm=vm)


def test_all_media_services_visible_even_if_system_admin_section_hidden():
    page = render_html({name: True for name in APPS})
    assert '<html lang="ru">' in page
    assert "http://192.0.2.238:32400/web/" in page
    assert 'id="management"' in page
    for app, (title, port) in APPS.items():
        assert f"http://192.0.2.238{port}" in page, app
        assert title in page
    assert "Proxmox VE" not in page
    assert "Home Assistant" in page
    assert "Проводник" in page
    assert "Finder" in page
    assert "Сохраните эту страницу в закладки" in page
    assert "password=" not in page.lower()


def test_only_enabled_apps_have_links_and_system_admin_separate():
    page = render_html({name: False for name in APPS}, admin=True)
    assert "http://192.0.2.238:32400/web/" in page
    for name, (_, port) in APPS.items():
        assert "http://192.0.2.238" + port not in page, name
    assert "Proxmox VE" in page
    assert 'id="system"' in page


def test_bonjour_announces_smb_and_http_portal():
    text = BONJOUR.read_text().replace(
        "{{ media_vm.discovery.friendly_name | default(media_vm.name) | e }}",
        "Media Server")
    # ElementTree permits the local avahi-service.dtd reference without fetching it.
    root = ET.fromstring(text)
    services = {(s.findtext("type"), s.findtext("port"))
                for s in root.findall("service")}
    assert services == {("_smb._tcp", "445"), ("_http._tcp", "80")}
    assert "path=/" in text


def test_portal_worker_serves_and_checks_enabled_link_directory():
    steps = yaml.safe_load(PORTAL_TASKS.read_text())
    names = [x["name"] for x in steps]
    assert names.index("Render links for enabled services") < names.index(
        "Enable local home portal")
    task = steps[names.index(
        "Verify local portal is responding with the application directory")]
    assert task["ansible.builtin.uri"]["return_content"] is True
    verify = steps[names.index(
        "Require landing page to list every enabled media application")]
    assertions = "\n".join(verify["ansible.builtin.assert"]["that"])
    assert all(title in assertions for title, _ in APPS.values())
