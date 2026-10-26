# SPDX-License-Identifier: GPL-2.0
"""Exercise the application's navigation without a display or source requests."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from gi.repository import Gio


TREE = ast.parse((Path(__file__).resolve().parents[1] / "haltia-video").read_text())
VIDEO = next(node for node in TREE.body if isinstance(node, ast.ClassDef)
             and node.name == "Video")


def method(name):
    node = next(node for node in VIDEO.body if isinstance(node, ast.FunctionDef)
                and node.name == name)
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "haltia-video", "exec"), namespace)
    return namespace[name]


class NavigationTest(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(page="home", sub=None, bookmark_query="")
        for name in ("header", "back_btn", "entry"):
            setattr(self.app, name, Mock())
        for name in ("left", "mark_tab", "say", "show_home", "show_feed",
                     "show_channels", "show_history", "show_bookmarks", "update_scope"):
            setattr(self.app, name, Mock())
        for name in ("go_back", "update_back", "show_page", "open_sub"):
            setattr(self.app, name, method(name).__get__(self.app))
        self.actions = Gio.SimpleActionGroup()
        self.app.add_action = self.actions.add_action
        # Use the application's actual action setup, without starting workers.
        init = next(node for node in VIDEO.body if isinstance(node, ast.FunctionDef)
                    and node.name == "__init__")
        start = next(i for i, node in enumerate(init.body)
                     if isinstance(node, ast.Assign)
                     and ast.unparse(node.targets[0]) == "self.back_action")
        exec(compile(ast.Module(body=init.body[start:], type_ignores=[]),
                     "haltia-video", "exec"), {"self": self.app, "Gio": Gio})

    def back(self):
        self.actions.activate_action("back", None)

    def test_root_has_no_back_step(self):
        self.assertFalse(self.actions.get_action_enabled("back"))
        self.back()
        self.app.show_home.assert_not_called()
        self.app.show_page("home")
        self.app.back_btn.set_visible.assert_called_with(False)

    def test_tabs_return_home(self):
        for page in ("feed", "channels", "bookmarks", "history"):
            with self.subTest(page=page):
                self.app.show_page(page)
                self.assertTrue(self.actions.get_action_enabled("back"))
                self.back()
                self.assertEqual(self.app.page, "home")
                self.assertFalse(self.actions.get_action_enabled("back"))

    def test_subpages_return_to_their_tab(self):
        for page in ("home", "feed", "channels", "bookmarks", "history"):
            for kind in ("search", "channels", "channel", "settings", "bookmark_link"):
                with self.subTest(page=page, kind=kind):
                    self.app.show_page(page)
                    self.app.open_sub({"kind": kind}, "Title")
                    self.assertTrue(self.actions.get_action_enabled("back"))
                    self.app.back_btn.set_visible.assert_called_with(True)
                    self.back()
                    self.assertIsNone(self.app.sub)
                    self.assertEqual(self.app.page, page)
                    self.assertEqual(self.actions.get_action_enabled("back"), page != "home")

    def test_header_reserves_corner_for_action_button(self):
        activate = ast.unparse(next(node for node in VIDEO.body
                                   if isinstance(node, ast.FunctionDef)
                                   and node.name == "do_activate"))
        self.assertIn("self.back_btn.set_action_name('app.back')", activate)
        self.assertIn("back_slot.set_size_request(110, -1)", activate)
        self.assertIn("back_slot.pack_start(self.back_btn, False, False, 0)", activate)
        self.assertEqual([line.strip() for line in activate.splitlines()
                          if "header.pack_start(" in line], ["header.pack_start(back_slot)"])
