"""GUI tests: real mouse events on the preview, through to the document."""

import math

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from to_pdf.model import resolve
from to_pdf.ui.image_list import drop_order
from to_pdf.ui.image_tool import ImageTool


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setOrganizationName("to-pdf-tests")
    yield a


@pytest.fixture
def window(app, tmp_path):
    paths = []
    for i, size in enumerate([(800, 600), (600, 900), (1200, 500)]):
        p = tmp_path / f"img{i}.png"
        Image.new("RGB", size, (40 * i, 120, 200)).save(p)
        paths.append(str(p))
    QSettings().clear()  # windows save page settings on close; start every test clean
    w = ImageTool()
    w.resize(1200, 800)
    w.show()
    w.add_paths(paths)
    app.processEvents()
    yield w
    w.close()


def _send(view, kind, scene_pt, buttons=Qt.MouseButton.LeftButton, button=Qt.MouseButton.LeftButton):
    vp = view.mapFromScene(scene_pt)
    local = QPointF(vp)
    glob = QPointF(view.viewport().mapToGlobal(vp))
    ev = QMouseEvent(kind, local, local, glob, button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), ev)
    QApplication.processEvents()


def drag(view, start, end, steps=8):
    _send(view, QMouseEvent.Type.MouseButtonPress, start)
    for k in range(1, steps + 1):
        t = k / steps
        _send(view, QMouseEvent.Type.MouseMove, start + (end - start) * t)
    _send(view, QMouseEvent.Type.MouseButtonRelease, end, buttons=Qt.MouseButton.NoButton)


def select_first(w):
    e = w.doc.entries[0]
    w._select_ids([e.id])
    QApplication.processEvents()
    item = w.view._items[e.id]
    w.view.centerOn(item.page_rect.center())
    QApplication.processEvents()
    return e, item


def test_add_builds_list_and_pages(window):
    assert window.list.count() == 3
    assert len(window.view._pages) == 3
    assert window.list.item(0).data(Qt.ItemDataRole.UserRole + 2) == 1


def test_drag_moves_image(window):
    e, item = select_first(window)
    g0 = resolve(e, window.doc.settings)
    start = item.pos()
    drag(window.view, start, start + QPointF(40, 25))
    g1 = resolve(window.doc.get(e.id), window.doc.settings)
    assert g1.cx == pytest.approx(g0.cx + 40, abs=1.0)
    assert g1.cy == pytest.approx(g0.cy + 25, abs=1.0)
    assert g1.w == pytest.approx(g0.w, rel=1e-6)
    window.doc.undo()
    g2 = resolve(window.doc.get(e.id), window.doc.settings)
    assert (g2.cx, g2.cy) == pytest.approx((g0.cx, g0.cy))


def test_corner_resize_keeps_aspect_and_anchor(window):
    e, item = select_first(window)
    g0 = resolve(e, window.doc.settings)
    anchor = item.mapToScene(item.corner_point(-1, -1))
    corner = item.mapToScene(item.corner_point(1, 1))
    target = anchor + (corner - anchor) * 0.5
    drag(window.view, corner, target)
    ent = window.doc.get(e.id)
    g1 = resolve(ent, window.doc.settings)
    assert g1.w == pytest.approx(g0.w * 0.5, rel=0.02)
    assert g1.w / g1.h == pytest.approx(ent.aspect, rel=1e-6)
    # opposite corner stayed put
    new_item = window.view._items[e.id]
    assert new_item.mapToScene(new_item.corner_point(-1, -1)).x() == pytest.approx(anchor.x(), abs=1.0)
    assert new_item.mapToScene(new_item.corner_point(-1, -1)).y() == pytest.approx(anchor.y(), abs=1.0)


def test_rotate_knob(window):
    e, item = select_first(window)
    knob = item.mapToScene(item.knob_point())
    center = item.pos()
    r = math.hypot(knob.x() - center.x(), knob.y() - center.y())
    # move the knob from 12 o'clock to roughly 1:30 -> +45 deg (clockwise on screen)
    a = math.radians(-90 + 45)
    target = QPointF(center.x() + r * math.cos(a), center.y() + r * math.sin(a))
    drag(window.view, knob, target)
    assert window.doc.get(e.id).placement.rotation == pytest.approx(45, abs=1.5)


def test_rotate_snaps_to_right_angle(window):
    e, item = select_first(window)
    knob = item.mapToScene(item.knob_point())
    center = item.pos()
    r = math.hypot(knob.x() - center.x(), knob.y() - center.y())
    a = math.radians(-90 + 88)  # 88 deg -> snaps to 90
    target = QPointF(center.x() + r * math.cos(a), center.y() + r * math.sin(a))
    drag(window.view, knob, target)
    assert window.doc.get(e.id).placement.rotation == pytest.approx(90, abs=1e-9)


def test_click_blank_page_area_selects_image(window):
    e = window.doc.entries[1]
    window.doc.set_geometry(e.id, 100, 100, 50, 0)  # small image in a corner
    QApplication.processEvents()
    item = window.view._items[e.id]
    window.view.centerOn(item.page_rect.center())
    blank = item.page_rect.bottomRight() - QPointF(30, 30)
    _send(window.view, QMouseEvent.Type.MouseButtonPress, blank)
    _send(window.view, QMouseEvent.Type.MouseButtonRelease, blank, buttons=Qt.MouseButton.NoButton)
    assert window.list.selected_ids() == [e.id]


def test_toolbar_rotate_and_margin_undo(window):
    e, _ = select_first(window)
    window.a_rot_r.trigger()
    assert window.doc.get(e.id).placement.rotation == 90
    window.margin_spin.setValue(20)
    window.margin_spin.setValue(25)
    assert window.doc.settings.margin_mm == 25
    window.doc.undo()  # both margin steps are one undo step
    assert window.doc.settings.margin_mm == 10
    window.doc.undo()
    assert window.doc.get(e.id).placement.rotation == 0


def test_remove_and_selection_follows(window):
    ids = [e.id for e in window.doc.entries]
    window._select_ids([ids[1]])
    window.remove_selected()
    assert [e.id for e in window.doc.entries] == [ids[0], ids[2]]
    assert window.list.selected_ids() == [ids[2]]


def test_new_document_keeps_settings_and_is_undoable(window):
    ids = [e.id for e in window.doc.entries]
    window.margin_spin.setValue(18)
    window.size_combo.setCurrentText("Letter")
    assert window.a_new.isEnabled()
    window.a_new.trigger()
    assert window.doc.entries == []
    assert window.list.count() == 0 and window.view._pages == []
    assert (window.doc.settings.size, window.doc.settings.margin_mm) == ("Letter", 18)
    assert not window.a_new.isEnabled() and not window.a_export.isEnabled()
    window.doc.undo()
    assert [e.id for e in window.doc.entries] == ids
    assert window.list.count() == 3


@pytest.mark.parametrize("moving,row,expected", [
    ({"b"}, 0, list("bacd")),
    ({"b"}, 4, list("acdb")),
    ({"b", "c"}, 4, list("adbc")),
    ({"a", "c"}, 2, list("bacd")),
    ({"d"}, 1, list("adbc")),
    ({"b"}, 1, list("abcd")),
    ({"b"}, 2, list("abcd")),
])
def test_drop_order(moving, row, expected):
    assert drop_order(list("abcd"), moving, row) == expected
