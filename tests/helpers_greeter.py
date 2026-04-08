# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import importlib
import sys
import types


def load_greeter_module(monkeypatch):
    timeout_calls = []

    class FakeBuilderInstance:
        def __init__(self):
            self.translation_domain = None
            self.loaded_path = None

        def set_translation_domain(self, domain):
            self.translation_domain = domain

        def add_from_file(self, path):
            self.loaded_path = path

        def get_object(self, name):
            return None

    class FakeBuilderClass:
        @staticmethod
        def new():
            return FakeBuilderInstance()

    class FakeApplication:
        def __init__(self, application_id=None, flags=None):
            self.application_id = application_id
            self.flags = flags
            self.connections = []
            self.run_called = False
            self.quit_called = False

        def connect(self, signal, callback):
            self.connections.append((signal, callback))

        def run(self):
            self.run_called = True

        def quit(self):
            self.quit_called = True

    class FakeStringList:
        def __init__(self):
            self.items = []

        def append(self, value):
            self.items.append(value)

    class FakeCssProvider:
        def __init__(self):
            self.loaded_paths = []

        def load_from_path(self, path):
            self.loaded_paths.append(path)

    fake_gtk = types.SimpleNamespace(
        Application=FakeApplication,
        Builder=FakeBuilderClass,
        StringList=FakeStringList,
        CssProvider=FakeCssProvider,
        StyleContext=types.SimpleNamespace(add_provider_for_display=lambda *args, **kwargs: None),
        STYLE_PROVIDER_PRIORITY_APPLICATION=1,
    )
    fake_gdk = types.SimpleNamespace(Display=types.SimpleNamespace(get_default=lambda: None))
    fake_gio = types.SimpleNamespace(
        ApplicationFlags=types.SimpleNamespace(FLAGS_NONE=0),
    )
    fake_glib = types.SimpleNamespace(timeout_add_seconds=lambda interval, callback: timeout_calls.append((interval, callback)) or 1)
    fake_repository = types.SimpleNamespace(Gtk=fake_gtk, Gdk=fake_gdk, Gio=fake_gio, GLib=fake_glib)
    fake_gi = types.SimpleNamespace(
        require_version=lambda *args, **kwargs: None,
        repository=fake_repository,
    )

    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repository)
    sys.modules.pop("wldm.greeter", None)

    module = importlib.import_module("wldm.greeter")
    module._test_timeout_calls = timeout_calls  # type: ignore[attr-defined]
    return module


class DummyClient:
    def write_message(self, message):
        return None

    def read_message(self):
        return None

    def can_read(self):
        return False

    def close(self):
        return None


class DummyLabel:
    def __init__(self):
        self.text = None
        self.visible = None
        self.tooltip = None
        self.width_chars = None

    def set_text(self, text):
        self.text = text

    def set_visible(self, value):
        self.visible = value

    def set_tooltip_text(self, text):
        self.tooltip = text

    def set_width_chars(self, value):
        self.width_chars = value


class DummyButton:
    def __init__(self):
        self.label = None
        self.visible = None
        self.sensitive = None
        self.can_default = None
        self.receives_default = None
        self.connections = []

    def set_label(self, text):
        self.label = text

    def set_visible(self, value):
        self.visible = value

    def set_sensitive(self, value):
        self.sensitive = value

    def set_can_default(self, value):
        self.can_default = value

    def set_receives_default(self, value):
        self.receives_default = value

    def connect(self, signal, callback):
        self.connections.append((signal, callback))


class StubWindow:
    def __init__(self):
        self.application = None
        self.default_widget = None
        self.presented = False

    def set_application(self, app):
        self.application = app

    def set_default_widget(self, widget):
        self.default_widget = widget

    def present(self):
        self.presented = True


class StubEntry:
    def __init__(self, text=""):
        self.text = text
        self.focused = False
        self.sensitive = None
        self.visible = None
        self.visibility = None
        self.show_peek_icon = None
        self.placeholder_text = None
        self.connections = []
        self.selected_region = None
        self.position = None

    def connect(self, signal, callback):
        self.connections.append((signal, callback))

    def get_text(self):
        return self.text

    def set_text(self, text):
        self.text = text

    def grab_focus(self):
        self.focused = True

    def set_sensitive(self, value):
        self.sensitive = value

    def set_visible(self, value):
        self.visible = value

    def set_visibility(self, value):
        self.visibility = value

    def set_show_peek_icon(self, value):
        self.show_peek_icon = value

    def set_placeholder_text(self, text):
        self.placeholder_text = text

    def select_region(self, start, end):
        self.selected_region = (start, end)

    def set_position(self, value):
        self.position = value


class StubSessionsEntry(StubEntry):
    def __init__(self, selected_item=None):
        super().__init__()
        self.model = None
        self.selected = None
        self.selected_item = selected_item

    def set_model(self, model):
        self.model = model

    def set_selected(self, index):
        self.selected = index

    def get_selected_item(self):
        if self.selected_item is not None:
            return self.selected_item
        if self.model is None or self.selected is None:
            return None
        return types.SimpleNamespace(get_string=lambda: self.model.items[self.selected])


class StubBox:
    def __init__(self):
        self.visible = None

    def set_visible(self, value):
        self.visible = value


class StubStatusLabel(DummyLabel):
    def __init__(self):
        super().__init__()
        self.added = []
        self.removed = []

    def add_css_class(self, name):
        self.added.append(name)

    def remove_css_class(self, name):
        self.removed.append(name)


def selected_item(name):
    return types.SimpleNamespace(get_string=lambda: name)


def selected_entry(name):
    return types.SimpleNamespace(get_selected_item=lambda: selected_item(name))


def new_greeter_app(greeter, **attrs):
    app = greeter.GreeterApp.__new__(greeter.GreeterApp)
    defaults = {
        "auth_in_progress": False,
        "conversation_pending": False,
        "conversation_prompt_style": "",
        "conversation_prompt_text": "",
        "session_ready": False,
        "auth_username": "",
        "last_username": "",
        "last_session_command": "",
        "sessions": [],
        "username_entry": None,
        "password_entry": None,
        "sessions_entry": None,
        "login_button": None,
        "cancel_button": None,
        "status_label": None,
        "session_label": None,
        "identity_preview": None,
        "identity_label": None,
        "avatar_label": None,
        "date_label": None,
        "time_label": None,
        "keyboard_label": None,
        "quit": False,
    }
    defaults.update(attrs)

    for name, value in defaults.items():
        setattr(app, name, value)

    return app


class StubBuilder:
    def __init__(self, objects=None, loaded_paths=None, add_error=None):
        self.objects = objects or {}
        self.loaded_paths = loaded_paths
        self.add_error = add_error
        self.translation_domain = None
        self.loaded_path = None

    def set_translation_domain(self, domain):
        self.translation_domain = domain

    def add_from_file(self, path):
        self.loaded_path = path
        if self.loaded_paths is not None:
            self.loaded_paths.append(path)
        if self.add_error is not None:
            raise self.add_error

    def get_object(self, name):
        return self.objects.get(name)


def make_activate_objects():
    window = StubWindow()
    username_entry = StubEntry()
    password_entry = StubEntry()
    sessions_entry = StubSessionsEntry()
    login_button = DummyButton()
    cancel_button = DummyButton()
    quit_button = DummyButton()
    reboot_button = DummyButton()
    suspend_button = DummyButton()
    hibernate_button = DummyButton()
    objects = {
        "main_window": window,
        "username_entry": username_entry,
        "password_entry": password_entry,
        "sessions_entry": sessions_entry,
        "status_label": DummyLabel(),
        "login_button": login_button,
        "cancel_button": cancel_button,
        "quit_button": quit_button,
        "reboot_button": reboot_button,
        "suspend_button": suspend_button,
        "hibernate_button": hibernate_button,
        "hostname_label": DummyLabel(),
        "date_label": DummyLabel(),
        "time_label": DummyLabel(),
        "keyboard_label": DummyLabel(),
        "session_label": DummyLabel(),
        "identity_preview": StubBox(),
        "identity_label": DummyLabel(),
        "avatar_label": DummyLabel(),
    }
    return objects
