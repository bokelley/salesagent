"""Static accessibility contracts for the signals authoring page."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates/tenant_signals_list.html"
SCRIPT = ROOT / "static/js/tenant_signals.js"


def test_overflow_menus_expose_aria_roles_and_state():
    html = TEMPLATE.read_text()

    assert 'class="menu-trigger" aria-label="More actions" aria-haspopup="menu" aria-expanded="false"' in html
    assert html.count('role="menu"') == html.count('class="menu"')
    assert html.count('role="menuitem" tabindex="-1"') == html.count('class="menu__item')
    assert html.count('role="separator"') == html.count('class="menu__sep"')


def test_decorative_signal_icons_are_hidden_from_assistive_tech():
    html = TEMPLATE.read_text()
    js = SCRIPT.read_text()

    assert 'aria-hidden="true" focusable="false"' in html
    assert 'aria-hidden="true" focusable="false"' in js


def test_menu_keyboard_controls_and_inline_rename_label_are_wired():
    js = SCRIPT.read_text()

    for token in ("ArrowDown", "ArrowUp", "Home", "End", "Escape", "Tab"):
        assert token in js
    assert "item.click()" in js
    assert "aria-expanded', 'true'" in js
    assert "aria-expanded', 'false'" in js
    assert 'aria-label="Rename signal ${escapeAttr(original)}"' in js
