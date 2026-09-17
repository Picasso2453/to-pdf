"""Document model: images, their placement on pages, page settings, undo.

Pure Python (no Qt) so the geometry can be tested without a GUI. All page
geometry is in PDF points (1/72 inch) with the origin at the page's top-left
and y pointing down, which is what the preview uses. The exporter flips y.

A placement is stored *relative* to the page: the image centre as a fraction
of the page size and its width as a multiple of the "fit" width (the largest
width at which the rotated image fits inside the margins). That way changing
the paper size or margins keeps each user adjustment proportionally intact.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable

MM = 72.0 / 25.4
PX_TO_PT = 0.75  # images without a physical size are laid out at 96 dpi

PAGE_SIZES: dict[str, tuple[float, float]] = {
    "A4": (595.28, 841.89),
    "Letter": (612.0, 792.0),
    "A3": (841.89, 1190.55),
    "A5": (419.53, 595.28),
    "Legal": (612.0, 1008.0),
}
FIT_TO_IMAGE = "Fit to image"
PAGE_SIZE_CHOICES = [*PAGE_SIZES, FIT_TO_IMAGE]
ORIENTATIONS = ("auto", "portrait", "landscape")

MIN_WIDTH_PT = 8.0
UNDO_LIMIT = 200


@dataclass
class PageSettings:
    size: str = "A4"
    orientation: str = "auto"  # auto | portrait | landscape
    margin_mm: float = 10.0


@dataclass
class Placement:
    cx: float = 0.5  # image centre, fraction of page width
    cy: float = 0.5  # image centre, fraction of page height
    zoom: float = 1.0  # image width / fit width
    rotation: float = 0.0  # degrees, clockwise on screen
    landscape: bool = False  # page orientation when settings say "auto"


@dataclass
class ImageEntry:
    path: str
    px_w: int  # size as displayed, i.e. after EXIF orientation
    px_h: int
    placement: Placement = field(default_factory=Placement)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def aspect(self) -> float:
        return self.px_w / self.px_h

    @property
    def name(self) -> str:
        return re.split(r"[\\/]", self.path)[-1]


@dataclass(frozen=True)
class Geometry:
    """Absolute layout of one page, in points."""

    page_w: float
    page_h: float
    cx: float
    cy: float
    w: float
    h: float
    rotation: float


# --------------------------------------------------------------------------
# geometry


def normalize_angle(deg: float) -> float:
    """Map to (-180, 180]."""
    deg = math.fmod(deg, 360.0)
    if deg <= -180.0:
        deg += 360.0
    elif deg > 180.0:
        deg -= 360.0
    return 0.0 if abs(deg) < 1e-9 else deg


def is_quarter_turned(rotation: float) -> bool:
    """True when the rotation is closer to 90/270 than to 0/180."""
    return round(rotation / 90.0) % 2 == 1


def natural_landscape(entry: ImageEntry, rotation: float | None = None) -> bool:
    rot = entry.placement.rotation if rotation is None else rotation
    w, h = entry.px_w, entry.px_h
    if is_quarter_turned(rot):
        w, h = h, w
    return w > h


def page_size(entry: ImageEntry, settings: PageSettings,
              placement: Placement | None = None) -> tuple[float, float]:
    p = placement or entry.placement
    if settings.size == FIT_TO_IMAGE:
        m = settings.margin_mm * MM
        w, h = entry.px_w * PX_TO_PT, entry.px_h * PX_TO_PT
        if is_quarter_turned(p.rotation):
            w, h = h, w
        return w + 2 * m, h + 2 * m
    pw, ph = PAGE_SIZES.get(settings.size, PAGE_SIZES["A4"])
    if settings.orientation == "landscape":
        landscape = True
    elif settings.orientation == "portrait":
        landscape = False
    else:
        landscape = p.landscape
    return (ph, pw) if landscape else (pw, ph)


def _content_box(page_w: float, page_h: float, margin_mm: float) -> tuple[float, float]:
    m = margin_mm * MM
    return max(page_w - 2 * m, 1.0), max(page_h - 2 * m, 1.0)


def _rotated_extent(aspect: float, rotation: float) -> tuple[float, float]:
    """Bounding box of a rotated image of width 1, as (bw, bh)."""
    a = math.radians(rotation)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return c + s / aspect, s + c / aspect


def fit_width(page_w: float, page_h: float, margin_mm: float,
              aspect: float, rotation: float) -> float:
    """Largest image width whose rotated bounding box fits in the margins."""
    aw, ah = _content_box(page_w, page_h, margin_mm)
    bw, bh = _rotated_extent(aspect, rotation)
    return min(aw / bw, ah / bh)


def fill_width(page_w: float, page_h: float, margin_mm: float,
               aspect: float, rotation: float) -> float:
    """Smallest image width whose rotated bounding box covers the margins."""
    aw, ah = _content_box(page_w, page_h, margin_mm)
    bw, bh = _rotated_extent(aspect, rotation)
    return max(aw / bw, ah / bh)


def resolve(entry: ImageEntry, settings: PageSettings) -> Geometry:
    p = entry.placement
    pw, ph = page_size(entry, settings)
    w = fit_width(pw, ph, settings.margin_mm, entry.aspect, p.rotation) * p.zoom
    return Geometry(pw, ph, p.cx * pw, p.cy * ph, w, w / entry.aspect, p.rotation)


def placement_from_geometry(entry: ImageEntry, settings: PageSettings,
                            cx: float, cy: float, w: float, rotation: float) -> Placement:
    """Inverse of resolve(): absolute points on the current page -> Placement."""
    old = entry.placement
    pw, ph = page_size(entry, settings)
    new = replace(old, cx=cx / pw, cy=cy / ph, rotation=normalize_angle(rotation))
    npw, nph = page_size(entry, settings, new)
    fw = fit_width(npw, nph, settings.margin_mm, entry.aspect, new.rotation)
    new.zoom = max(w, MIN_WIDTH_PT) / fw
    return new


# --------------------------------------------------------------------------
# document


def natural_key(text: str) -> list:
    """Sort key where 'img2' < 'img10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


Snapshot = tuple[list[ImageEntry], PageSettings]


class Document:
    def __init__(self) -> None:
        self.entries: list[ImageEntry] = []
        self.settings = PageSettings()
        self._undo: list[Snapshot] = []
        self._redo: list[Snapshot] = []
        self._coalesce: str | None = None
        self._listeners: list[Callable[[], None]] = []

    # -- plumbing ---------------------------------------------------------

    def subscribe(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def _changed(self) -> None:
        for fn in self._listeners:
            fn()

    def _snapshot(self) -> Snapshot:
        return ([replace(e, placement=replace(e.placement)) for e in self.entries],
                replace(self.settings))

    def _checkpoint(self, coalesce: str | None = None) -> None:
        """Record the state before a change. Repeated changes sharing a
        coalesce key (e.g. spinning the margin box) collapse into one step."""
        if coalesce is not None and coalesce == self._coalesce:
            return
        self._coalesce = coalesce
        self._undo.append(self._snapshot())
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()

    def _restore(self, snap: Snapshot) -> None:
        entries, settings = snap
        self.entries = [replace(e, placement=replace(e.placement)) for e in entries]
        self.settings = replace(settings)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        self._coalesce = None
        self._changed()

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        self._coalesce = None
        self._changed()

    def get(self, entry_id: str) -> ImageEntry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def index_of(self, entry_id: str) -> int:
        return next((i for i, e in enumerate(self.entries) if e.id == entry_id), -1)

    # -- edits ------------------------------------------------------------

    def add(self, entries: Iterable[ImageEntry], index: int | None = None) -> None:
        entries = list(entries)
        if not entries:
            return
        self._checkpoint()
        for e in entries:
            e.placement.landscape = natural_landscape(e)
        at = len(self.entries) if index is None else max(0, min(index, len(self.entries)))
        self.entries[at:at] = entries
        self._changed()

    def remove(self, ids: Iterable[str]) -> None:
        ids = set(ids)
        if not any(e.id in ids for e in self.entries):
            return
        self._checkpoint()
        self.entries = [e for e in self.entries if e.id not in ids]
        self._changed()

    def clear(self) -> None:
        if self.entries:
            self._checkpoint()
            self.entries = []
            self._changed()

    def reorder(self, ids: list[str]) -> None:
        """Set the order; ids not listed keep their relative order at the end."""
        by_id = {e.id: e for e in self.entries}
        new = [by_id[i] for i in ids if i in by_id]
        seen = {e.id for e in new}
        new += [e for e in self.entries if e.id not in seen]
        if [e.id for e in new] == [e.id for e in self.entries]:
            return
        self._checkpoint()
        self.entries = new
        self._changed()

    def move(self, ids: Iterable[str], delta: int) -> None:
        """Shift the given entries up (delta<0) or down, as a block."""
        ids = set(ids)
        order = list(self.entries)
        step = -1 if delta < 0 else 1
        for _ in range(abs(delta)):
            span = range(len(order)) if step < 0 else range(len(order) - 1, -1, -1)
            for i in span:
                j = i + step
                if order[i].id in ids and 0 <= j < len(order) and order[j].id not in ids:
                    order[i], order[j] = order[j], order[i]
        self.reorder([e.id for e in order])

    def sort_by_name(self) -> None:
        self.reorder([e.id for e in sorted(self.entries, key=lambda e: natural_key(e.name))])

    def set_placement(self, entry_id: str, placement: Placement) -> None:
        e = self.get(entry_id)
        if e is None or e.placement == placement:
            return
        self._checkpoint()
        e.placement = placement
        self._changed()

    def set_geometry(self, entry_id: str, cx: float, cy: float, w: float, rotation: float) -> None:
        e = self.get(entry_id)
        if e is not None:
            self.set_placement(entry_id, placement_from_geometry(e, self.settings, cx, cy, w, rotation))

    def _edit_many(self, ids: Iterable[str], fn: Callable[[ImageEntry], Placement]) -> None:
        ids = set(ids)
        targets = [e for e in self.entries if e.id in ids]
        changes = [(e, fn(e)) for e in targets]
        changes = [(e, p) for e, p in changes if p != e.placement]
        if not changes:
            return
        self._checkpoint()
        for e, p in changes:
            e.placement = p
        self._changed()

    def rotate(self, ids: Iterable[str], delta: float) -> None:
        """Rotate by delta degrees; quarter turns also turn an auto-oriented page."""
        def fn(e: ImageEntry) -> Placement:
            rot = normalize_angle(e.placement.rotation + delta)
            p = replace(e.placement, rotation=rot)
            if is_quarter_turned(delta):
                p.landscape = natural_landscape(e, rot)
            return p
        self._edit_many(ids, fn)

    def fit(self, ids: Iterable[str]) -> None:
        self._edit_many(ids, lambda e: replace(
            e.placement, cx=0.5, cy=0.5, zoom=1.0, landscape=natural_landscape(e)))

    def fill(self, ids: Iterable[str]) -> None:
        def fn(e: ImageEntry) -> Placement:
            p = replace(e.placement, cx=0.5, cy=0.5, landscape=natural_landscape(e))
            pw, ph = page_size(e, self.settings, p)
            args = (pw, ph, self.settings.margin_mm, e.aspect, p.rotation)
            p.zoom = fill_width(*args) / fit_width(*args)
            return p
        self._edit_many(ids, fn)

    def center(self, ids: Iterable[str]) -> None:
        self._edit_many(ids, lambda e: replace(e.placement, cx=0.5, cy=0.5))

    def reset(self, ids: Iterable[str]) -> None:
        self._edit_many(ids, lambda e: Placement(landscape=natural_landscape(e, 0.0)))

    def set_settings(self, settings: PageSettings, coalesce: str | None = None) -> None:
        if settings == self.settings:
            return
        self._checkpoint(coalesce)
        self.settings = replace(settings)
        self._changed()
