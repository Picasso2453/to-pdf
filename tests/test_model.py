import math

import pytest
from dataclasses import replace

from to_pdf.model import (
    Document,
    ImageEntry,
    PageSettings,
    Placement,
    FIT_TO_IMAGE,
    MM,
    PX_TO_PT,
    PAGE_SIZES,
    fit_width,
    fill_width,
    is_quarter_turned,
    natural_key,
    natural_landscape,
    normalize_angle,
    page_size,
    placement_from_geometry,
    resolve,
    _content_box,
    _rotated_extent,
)


def make_entry(w=800, h=600, path="img.jpg", rotation=0.0):
    e = ImageEntry(path=path, px_w=w, px_h=h)
    e.placement = replace(e.placement, rotation=rotation)
    return e


# --------------------------------------------------------------------------
# normalize_angle


@pytest.mark.parametrize(
    "deg,expected",
    [
        (0, 0),
        (90, 90),
        (180, 180),
        (-180, 180),
        (270, -90),
        (-270, 90),
        (360, 0),
        (-360, 0),
        (450, 90),
        (-450, -90),
        (181, -179),
        (-181, 179),
    ],
)
def test_normalize_angle(deg, expected):
    assert normalize_angle(deg) == pytest.approx(expected, abs=1e-9)


def test_normalize_angle_range():
    for deg in range(-720, 721, 17):
        n = normalize_angle(deg)
        assert -180.0 < n <= 180.0 + 1e-9


# --------------------------------------------------------------------------
# fit_width / fill_width


def _check_touches_margin(page_w, page_h, margin_mm, aspect, rotation, w, kind):
    aw, ah = _content_box(page_w, page_h, margin_mm)
    bw, bh = _rotated_extent(aspect, rotation)
    box_w, box_h = bw * w, bh * w
    tol = 1e-6
    if kind == "fit":
        # touches on the binding axis, within (<=) on the other
        assert box_w <= aw + tol
        assert box_h <= ah + tol
        assert math.isclose(box_w, aw, abs_tol=1e-6) or math.isclose(box_h, ah, abs_tol=1e-6)
    else:
        # fill: covers (>=) both axes, touches (==) on the binding axis
        assert box_w >= aw - tol
        assert box_h >= ah - tol
        assert math.isclose(box_w, aw, abs_tol=1e-6) or math.isclose(box_h, ah, abs_tol=1e-6)


@pytest.mark.parametrize("rotation", [0, 90, 45])
@pytest.mark.parametrize("aspect", [1.0, 4 / 3, 3 / 4, 2.0])
def test_fit_width_touches_margin_box(rotation, aspect):
    page_w, page_h, margin_mm = 595.28, 841.89, 10.0
    w = fit_width(page_w, page_h, margin_mm, aspect, rotation)
    assert w > 0
    _check_touches_margin(page_w, page_h, margin_mm, aspect, rotation, w, "fit")


@pytest.mark.parametrize("rotation", [0, 90, 45])
@pytest.mark.parametrize("aspect", [1.0, 4 / 3, 3 / 4, 2.0])
def test_fill_width_covers_margin_box(rotation, aspect):
    page_w, page_h, margin_mm = 595.28, 841.89, 10.0
    w = fill_width(page_w, page_h, margin_mm, aspect, rotation)
    assert w > 0
    _check_touches_margin(page_w, page_h, margin_mm, aspect, rotation, w, "fill")


def test_fit_width_0_and_90_symmetry():
    # A wide image fit at 0deg should have the same bounding box as a tall
    # (1/aspect) image fit at 90deg, since 90deg just swaps the axes.
    page_w, page_h, margin_mm = 595.28, 841.89, 10.0
    aspect = 1.6
    w0 = fit_width(page_w, page_h, margin_mm, aspect, 0)
    w90 = fit_width(page_w, page_h, margin_mm, aspect, 90)
    aw, ah = _content_box(page_w, page_h, margin_mm)
    # at rotation 0: box is (w0, w0/aspect); at rotation 90: box is (w90/aspect, w90)
    assert math.isclose(w0, min(aw, ah * aspect), rel_tol=1e-9)
    assert math.isclose(w90 / aspect, min(aw / aspect, ah), rel_tol=1e-9) or math.isclose(
        w90, min(aw * aspect, ah), rel_tol=1e-9
    )


# --------------------------------------------------------------------------
# page_size


def test_page_size_a4_portrait():
    settings = PageSettings(size="A4", orientation="portrait")
    e = make_entry()
    w, h = page_size(e, settings)
    assert (w, h) == PAGE_SIZES["A4"]


def test_page_size_a4_landscape():
    settings = PageSettings(size="A4", orientation="landscape")
    e = make_entry()
    w, h = page_size(e, settings)
    pw, ph = PAGE_SIZES["A4"]
    assert (w, h) == (ph, pw)


def test_page_size_a4_auto_follows_placement_landscape():
    settings = PageSettings(size="A4", orientation="auto")
    e = make_entry()
    pw, ph = PAGE_SIZES["A4"]

    e.placement.landscape = False
    assert page_size(e, settings) == (pw, ph)

    e.placement.landscape = True
    assert page_size(e, settings) == (ph, pw)


def test_page_size_fit_to_image():
    settings = PageSettings(size=FIT_TO_IMAGE, margin_mm=10.0)
    e = make_entry(w=800, h=600, rotation=0.0)
    m = settings.margin_mm * MM
    w, h = page_size(e, settings)
    assert w == pytest.approx(800 * PX_TO_PT + 2 * m)
    assert h == pytest.approx(600 * PX_TO_PT + 2 * m)


def test_page_size_fit_to_image_quarter_turn_swaps():
    settings = PageSettings(size=FIT_TO_IMAGE, margin_mm=10.0)
    e = make_entry(w=800, h=600, rotation=90.0)
    m = settings.margin_mm * MM
    w, h = page_size(e, settings)
    assert w == pytest.approx(600 * PX_TO_PT + 2 * m)
    assert h == pytest.approx(800 * PX_TO_PT + 2 * m)


def test_page_size_fit_to_image_45deg_does_not_swap():
    settings = PageSettings(size=FIT_TO_IMAGE, margin_mm=10.0)
    e = make_entry(w=800, h=600, rotation=45.0)
    m = settings.margin_mm * MM
    w, h = page_size(e, settings)
    assert w == pytest.approx(800 * PX_TO_PT + 2 * m)
    assert h == pytest.approx(600 * PX_TO_PT + 2 * m)


# --------------------------------------------------------------------------
# resolve / placement_from_geometry round trip


@pytest.mark.parametrize("rotation", [0.0, 30.0, 90.0, 137.5])
@pytest.mark.parametrize(
    "placement_kwargs",
    [
        dict(cx=0.5, cy=0.5, zoom=1.0),
        dict(cx=0.3, cy=0.7, zoom=1.4),
        dict(cx=0.1, cy=0.9, zoom=0.6),
    ],
)
def test_resolve_placement_roundtrip(rotation, placement_kwargs):
    e = make_entry(w=1000, h=400)
    e.placement = Placement(rotation=rotation, **placement_kwargs)
    settings = PageSettings(size="A4", orientation="auto", margin_mm=12.0)

    g = resolve(e, settings)
    new_placement = placement_from_geometry(e, settings, g.cx, g.cy, g.w, g.rotation)

    assert new_placement.cx == pytest.approx(e.placement.cx, abs=1e-9)
    assert new_placement.cy == pytest.approx(e.placement.cy, abs=1e-9)
    assert new_placement.zoom == pytest.approx(e.placement.zoom, abs=1e-9)
    assert normalize_angle(new_placement.rotation) == pytest.approx(
        normalize_angle(e.placement.rotation), abs=1e-9
    )


# --------------------------------------------------------------------------
# Document: add/remove/clear/reorder/move


def test_document_add_remove_clear():
    d = Document()
    e1 = make_entry(path="a.jpg")
    e2 = make_entry(path="b.jpg")
    d.add([e1, e2])
    assert [e.id for e in d.entries] == [e1.id, e2.id]

    d.remove([e1.id])
    assert [e.id for e in d.entries] == [e2.id]

    d.clear()
    assert d.entries == []


def test_document_add_sets_landscape_flag():
    d = Document()
    e = make_entry(w=800, h=400)  # landscape aspect
    d.add([e])
    assert d.entries[0].placement.landscape is True


def test_document_reorder():
    d = Document()
    e1, e2, e3 = make_entry(path="a"), make_entry(path="b"), make_entry(path="c")
    d.add([e1, e2, e3])
    d.reorder([e3.id, e1.id])
    assert [e.id for e in d.entries] == [e3.id, e1.id, e2.id]


def test_document_move_block_up():
    d = Document()
    a, b, c = make_entry(path="a"), make_entry(path="b"), make_entry(path="c")
    d.add([a, b, c])
    d.move([b.id, c.id], -1)
    assert [e.id for e in d.entries] == [b.id, c.id, a.id]


def test_document_move_block_down():
    d = Document()
    a, b, c = make_entry(path="a"), make_entry(path="b"), make_entry(path="c")
    d.add([a, b, c])
    d.move([a.id, b.id], 1)
    assert [e.id for e in d.entries] == [c.id, a.id, b.id]


def test_document_move_block_edge_is_noop():
    d = Document()
    a, b, c = make_entry(path="a"), make_entry(path="b"), make_entry(path="c")
    d.add([a, b, c])
    undo_len = len(d._undo)
    d.move([a.id], -1)  # already at top
    assert [e.id for e in d.entries] == [a.id, b.id, c.id]
    assert len(d._undo) == undo_len  # no-op: no checkpoint recorded


def test_document_sort_by_name_natural_order():
    d = Document()
    e10 = make_entry(path="img10.png")
    e2 = make_entry(path="img2.png")
    e1 = make_entry(path="img1.png")
    d.add([e10, e2, e1])
    d.sort_by_name()
    assert [e.name for e in d.entries] == ["img1.png", "img2.png", "img10.png"]


# --------------------------------------------------------------------------
# rotate / fit / fill / center / reset


def test_rotate_90_flips_auto_landscape():
    d = Document()
    e = make_entry(w=100, h=200)  # portrait
    d.add([e])
    assert d.entries[0].placement.landscape is False
    d.rotate([e.id], 90)
    assert d.entries[0].placement.landscape is True
    assert d.entries[0].placement.rotation == pytest.approx(90.0)


def test_rotate_non_quarter_does_not_flip_landscape():
    d = Document()
    e = make_entry(w=100, h=200)
    d.add([e])
    d.rotate([e.id], 45)
    assert d.entries[0].placement.landscape is False


def test_fit_resets_position_and_zoom():
    d = Document()
    e = make_entry(w=800, h=400)
    d.add([e])
    d.set_geometry(e.id, cx=100, cy=100, w=50, rotation=30)
    d.fit([e.id])
    p = d.entries[0].placement
    assert p.cx == pytest.approx(0.5)
    assert p.cy == pytest.approx(0.5)
    assert p.zoom == pytest.approx(1.0)


def test_fill_zoom_covers_page():
    d = Document()
    e = make_entry(w=800, h=400)
    d.add([e])
    d.fill([e.id])
    p = d.entries[0].placement
    settings = d.settings
    pw, ph = page_size(e, settings, p)
    fw = fit_width(pw, ph, settings.margin_mm, e.aspect, p.rotation)
    fillw = fill_width(pw, ph, settings.margin_mm, e.aspect, p.rotation)
    assert p.zoom == pytest.approx(fillw / fw)


def test_center_keeps_zoom_and_rotation():
    d = Document()
    e = make_entry(w=800, h=400)
    d.add([e])
    d.set_geometry(e.id, cx=200, cy=300, w=100, rotation=15)
    zoom_before = d.entries[0].placement.zoom
    rot_before = d.entries[0].placement.rotation
    d.center([e.id])
    p = d.entries[0].placement
    assert p.cx == pytest.approx(0.5)
    assert p.cy == pytest.approx(0.5)
    assert p.zoom == pytest.approx(zoom_before)
    assert p.rotation == pytest.approx(rot_before)


def test_reset_returns_to_defaults():
    d = Document()
    e = make_entry(w=100, h=200)
    d.add([e])
    d.rotate([e.id], 90)
    d.set_geometry(e.id, cx=200, cy=300, w=100, rotation=90)
    d.reset([e.id])
    p = d.entries[0].placement
    assert p.cx == pytest.approx(0.5)
    assert p.cy == pytest.approx(0.5)
    assert p.zoom == pytest.approx(1.0)
    assert p.rotation == pytest.approx(0.0)
    assert p.landscape is False  # natural_landscape at rotation 0 for a 100x200 image


# --------------------------------------------------------------------------
# set_settings coalescing


def test_set_settings_coalescing():
    d = Document()
    e = make_entry()
    d.add([e])
    base_len = len(d._undo)

    d.set_settings(replace(d.settings, margin_mm=5.0), coalesce="margin")
    assert len(d._undo) == base_len + 1

    d.set_settings(replace(d.settings, margin_mm=8.0), coalesce="margin")
    assert len(d._undo) == base_len + 1  # coalesced into the same step

    # an intervening, differently-keyed edit breaks the coalescing
    d.rotate([e.id], 30)
    assert len(d._undo) == base_len + 2

    d.set_settings(replace(d.settings, margin_mm=9.0), coalesce="margin")
    assert len(d._undo) == base_len + 3


# --------------------------------------------------------------------------
# undo/redo


def test_undo_redo_restores_entries_and_settings():
    d = Document()
    e1 = make_entry(path="a.jpg", w=800, h=600)
    e2 = make_entry(path="b.jpg", w=400, h=300)
    d.add([e1, e2])

    original_ids = [e.id for e in d.entries]
    original_placements = [replace(e.placement) for e in d.entries]
    original_settings = replace(d.settings)

    d.rotate([e1.id], 45)
    d.set_settings(replace(d.settings, size="Letter"))

    assert d.entries[0].placement.rotation == pytest.approx(45.0)
    assert d.settings.size == "Letter"

    d.undo()
    d.undo()

    assert [e.id for e in d.entries] == original_ids
    assert [e.placement for e in d.entries] == original_placements
    assert d.settings == original_settings

    d.redo()
    d.redo()

    assert d.entries[0].placement.rotation == pytest.approx(45.0)
    assert d.settings.size == "Letter"


def test_redo_cleared_by_new_edit():
    d = Document()
    e = make_entry()
    d.add([e])
    d.rotate([e.id], 30)
    d.undo()
    assert d.can_redo is True

    d.rotate([e.id], 60)
    assert d.can_redo is False


# --------------------------------------------------------------------------
# listeners


def test_listeners_fire_on_change_not_on_noop():
    d = Document()
    e = make_entry()
    calls = []
    d.subscribe(lambda: calls.append(1))

    d.add([e])
    assert len(calls) == 1

    d.remove(["does-not-exist"])
    assert len(calls) == 1  # no-op: listener not called

    d.set_placement(e.id, d.entries[0].placement)  # same placement: no-op
    assert len(calls) == 1

    d.rotate([e.id], 10)
    assert len(calls) == 2
