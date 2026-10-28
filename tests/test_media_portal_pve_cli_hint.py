"""Media portal has a quiet, usable owner-recovery CLI hint."""
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[1] /
        "roles/media_guest_setup/templates/media-portal-index.html.j2")


def test_footer_has_copyable_pve_root_command():
    source = PAGE.read_text(encoding="utf-8")
    footer = source.split("<footer>", 1)[1].split("</footer>", 1)[0]
    assert "PVE · root:" in footer
    assert 'value="/usr/local/sbin/dh-media-password-show"' in footer
    assert 'id="pve-root-copy"' in footer
    assert 'aria-label="Скопировать команду для root PVE"' in footer
    assert 'id="pve-root-copy-status"' in footer
    assert "password=" not in footer


def test_clipboard_works_in_http_with_selection_fallback():
    source = PAGE.read_text(encoding="utf-8")
    assert "window.isSecureContext && navigator.clipboard" in source
    assert 'document.execCommand("copy")' in source
    assert "field.setSelectionRange(0, field.value.length)" in source
    assert "Скопировано" in source
    assert "Выделено — Ctrl+C" in source
