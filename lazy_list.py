# SPDX-License-Identifier: GPL-2.0
"""A list of cards or rows built as it is scrolled to.

    LazyList(items, make, cols=None, spacing=12, at_least=0)
        .widget        what goes into the page: a flow box of cols cards a
                       row, or a column of rows (cols None)
        .built         how many items have their widget yet
        .locate(match) build up to the first item match(item) is true for,
                       then focus its widget and scroll it into the window
        .refresh()     every built widget reads its state again
    LazyList.refresh_all()  the same for every list still on a page

A page of 300 cards made whole kept the GTK thread busy for seconds after
it showed (each card is a dozen widgets for GTK to style and measure; on
the tablet some 25 ms), and every tap waited behind it. Only the first
screen is made at once; the next one when the bottom of the list comes
within AHEAD screens of what the window shows, a row at a time. Made widgets stay, so the
scroll stays smooth; their pictures go when they are more than RELEASE
screens away, or the page is hidden (a kept tab), and come back on return.

A widget make() returns may have:
    _refresh()      read its state again (the bookmark, the position)
    _pictures(on)   let its pictures go (False) or take them back (True)
"""
import itertools
import time
import weakref

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

AHEAD = 1.5  # screens below the window made ahead of the scroll
RELEASE = 3  # screens away from the window a widget's pictures go at
ROWS_AT_ONCE = 12  # rows (not cards) of a screen, made at once
# Made in one go later, while the person may be touching the screen: a
# row of cards or a few rows, so a tap waits for little (a card is some
# 25 ms on the tablet).
ROWS_PER_IDLE = 5
PICTURES_EVERY = 0.25  # s between looks at which pictures to keep


class LazyList:
    live = weakref.WeakSet()
    serials = itertools.count()

    def __init__(self, items, make, cols=None, spacing=12, at_least=0):
        self.items = list(items)
        self.make = make
        self.serial = next(LazyList.serials)  # the order lists were made in
        self.cols = cols
        if cols:
            self.widget = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                      max_children_per_line=cols, min_children_per_line=cols,
                                      row_spacing=16, column_spacing=16)
            first, self.batch = cols * 3, cols
        else:
            self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
            first, self.batch = ROWS_AT_ONCE, ROWS_PER_IDLE
        self.widget.set_valign(Gtk.Align.START)
        self.made = []  # the widgets, in the items' order
        self.held = {}  # index: whether its pictures are on
        self.alive = True
        self.queued = False
        self.pictures_at = 0.0
        self.pictures_later = None
        self.want = None  # the index locate() builds up to
        self.adjustment = None
        self.handlers = []
        self.scroll = None
        self.widget.connect("destroy", self._destroyed)
        self.widget.connect("size-allocate", self._queue)
        self.widget.connect("map", self._queue)
        self.widget.connect("unmap", lambda *_a: self._pictures_all(False))
        self.widget.connect("hierarchy-changed", self._attach)
        self._add(max(at_least, first))
        LazyList.live.add(self)

    @property
    def built(self):
        return len(self.made)

    @classmethod
    def refresh_all(cls):
        for lst in list(cls.live):
            lst.refresh()

    def refresh(self):
        for widget in self.made:
            fn = getattr(widget, "_refresh", None)
            if fn:
                fn()

    def locate(self, match, done=None):
        """Builds as far as the item, then brings it on screen; done(widget)
        when it is there."""
        index = next((i for i, item in enumerate(self.items) if match(item)), None)
        if index is None:
            return False
        self.want = (index, done)
        self._queue()
        return True

    # --- inside -----------------------------------------------------------
    def _add(self, n):
        start = len(self.made)
        for item in self.items[start:start + n]:
            widget = self.make(item)
            self.made.append(widget)
            self.widget.add(widget)
            if self.widget.get_visible():
                widget.show_all()

    def _attach(self, *_args):
        scroll = self.widget.get_ancestor(Gtk.ScrolledWindow)
        if scroll is self.scroll:
            return
        self._detach()
        self.scroll = scroll
        if scroll is not None:
            self.adjustment = scroll.get_vadjustment()
            # The adjustment outlives this list (the page's scrolled window
            # stays): its handlers go with the list.
            self.handlers = [self.adjustment.connect("value-changed", self._queue),
                             self.adjustment.connect("changed", self._queue)]

    def _detach(self):
        for handler in self.handlers:
            self.adjustment.disconnect(handler)
        self.handlers = []
        self.adjustment = None

    def _destroyed(self, *_args):
        self.alive = False
        self._detach()
        if self.pictures_later:
            GLib.source_remove(self.pictures_later)
            self.pictures_later = None
        LazyList.live.discard(self)
        self.made = []

    def _queue(self, *_args):
        if self.alive and not self.queued:
            self.queued = True
            GLib.idle_add(self._check, priority=GLib.PRIORITY_LOW)

    def _content(self):
        child = self.scroll.get_child()
        return child.get_child() if isinstance(child, Gtk.Viewport) else child

    def _top(self, widget):
        """Where widget is in the scrolled content, or None not laid out yet."""
        coords = widget.translate_coordinates(self._content(), 0, 0)
        if coords is None or widget.get_allocated_height() <= 1:
            return None
        return coords[1]

    def _check(self):
        self.queued = False
        if not self.alive or self.adjustment is None or not self.widget.get_mapped():
            return False
        adj = self.adjustment
        page = adj.get_page_size()
        top = self._top(self.widget)
        if page <= 1 or top is None:
            return False
        if self.want is not None and self.want[0] >= len(self.made):
            self._add(self.batch)  # on towards the one to show; the next batch on its allocation
            return False
        if self.want is not None:
            self._reveal()
        bottom = top + self.widget.get_allocated_height()
        if len(self.made) < len(self.items) and bottom < adj.get_value() + page * (1 + AHEAD):
            self._add(self.batch)
            return False
        self._pictures_soon()
        return False

    def _reveal(self):
        index, done = self.want
        widget = self.made[index]
        top = self._top(widget)
        adj = self.adjustment
        if top is None or not widget.get_mapped() or adj.get_upper() < top + widget.get_allocated_height() - 1:
            return  # again on the next allocation
        self.want = None
        widget.set_can_focus(True)
        widget.grab_focus()
        adj.set_value(max(adj.get_lower(), min(top, adj.get_upper() - adj.get_page_size())))
        if done:
            done(widget)

    def _pictures_soon(self):
        """Which pictures to keep, at most every PICTURES_EVERY: a fling
        calls this at every frame."""
        if self.pictures_later:
            return
        wait = self.pictures_at + PICTURES_EVERY - time.monotonic()
        if wait <= 0:
            self._pictures()
        else:
            self.pictures_later = GLib.timeout_add(int(wait * 1000) + 1, self._pictures_timer)

    def _pictures_timer(self):
        self.pictures_later = None
        if self.alive:
            self._pictures()
        return False

    def _pictures(self):
        self.pictures_at = time.monotonic()
        if self.adjustment is None or not self.widget.get_mapped():
            return
        adj = self.adjustment
        page = adj.get_page_size()
        low = adj.get_value() - RELEASE * page
        high = adj.get_value() + page + RELEASE * page
        for i, widget in enumerate(self.made):
            fn = getattr(widget, "_pictures", None)
            if fn is None:
                continue
            top = self._top(widget)
            on = top is None or (top + widget.get_allocated_height() >= low and top <= high)
            if self.held.get(i, True) != on:
                self.held[i] = on
                fn(on)

    def _pictures_all(self, on):
        for i, widget in enumerate(self.made):
            fn = getattr(widget, "_pictures", None)
            if fn is not None and self.held.get(i, True) != on:
                self.held[i] = on
                fn(on)
