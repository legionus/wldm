# SPDX-License-Identifier: GPL-2.0-or-later

PYTHON ?= python3
NULL :=

DESTDIR             ?=
SYSCONFDIR          ?= /etc
PREFIX              ?= /usr
DATADIR             ?= $(PREFIX)/share
BINDIR              ?= $(PREFIX)/sbin
SYSTEMDUNITDIR      ?= $(PREFIX)/lib/systemd/system
LOCALEDIR           ?= $(PREFIX)/share/locale
DBUSSYSTEMPOLICYDIR ?= $(DATADIR)/dbus-1/system.d

DBUSSERVICE ?= org.freedesktop.DisplayManager

GREETER_USER  ?= gdm
GREETER_GROUP ?= gdm
DBUS_USER     ?= gdm

WLDM_DATADIR := $(DATADIR)/wldm

CONFIG_TEMPLATE      := config/wldm.ini.in
LAUNCHER_TEMPLATE    := data/scripts/wldm.in
SERVICE_TEMPLATE     := data/systemd/wldm.service.in
DBUS_POLICY_TEMPLATE := data/dbus-1/system.d/wldm-dbus.conf.in

SUBST = \
	's|@bindir@|$(BINDIR)|g' \
	's|@datadir@|$(WLDM_DATADIR)|g' \
	's|@dbus_service@|$(DBUSSERVICE)|g' \
	's|@dbus_user@|$(DBUS_USER)|g' \
	's|@greeter_group@|$(GREETER_GROUP)|g' \
	's|@greeter_user@|$(GREETER_USER)|g' \
	's|@localedir@|$(LOCALEDIR)|g' \
	's|@python@|$(PYTHON)|g' \
	's|@sysconfdir@|$(SYSCONFDIR)|g' \
	$(NULL)

.PHONY: all install install-python install-launcher install-data install-config install-systemd install-dbus-policy uninstall

all:
	@:

install: install-python install-launcher install-data install-config install-systemd install-dbus-policy

install-python:
	$(PYTHON) -m pip install . --root $(DESTDIR) --no-deps --no-build-isolation --ignore-installed

install-launcher:
	install -d $(DESTDIR)$(BINDIR)
	sed $(addprefix -e ,$(SUBST)) $(LAUNCHER_TEMPLATE) > $(DESTDIR)$(BINDIR)/wldm
	chmod 0755 $(DESTDIR)$(BINDIR)/wldm

install-data:
	install -d $(DESTDIR)$(WLDM_DATADIR)/resources
	install -m 0644 data/resources/greeter.ui $(DESTDIR)$(WLDM_DATADIR)/resources/greeter.ui
	install -m 0644 data/resources/style.css $(DESTDIR)$(WLDM_DATADIR)/resources/style.css
	install -d $(DESTDIR)$(WLDM_DATADIR)/scripts
	install -m 0755 data/scripts/wayland-session $(DESTDIR)$(WLDM_DATADIR)/scripts/wayland-session

install-config:
	install -d $(DESTDIR)$(SYSCONFDIR)
	sed $(addprefix -e ,$(SUBST)) $(CONFIG_TEMPLATE) > $(DESTDIR)$(SYSCONFDIR)/wldm.ini
	chmod 0644 $(DESTDIR)$(SYSCONFDIR)/wldm.ini

install-systemd:
	install -d $(DESTDIR)$(SYSTEMDUNITDIR)
	sed $(addprefix -e ,$(SUBST)) $(SERVICE_TEMPLATE) > $(DESTDIR)$(SYSTEMDUNITDIR)/wldm.service
	chmod 0644 $(DESTDIR)$(SYSTEMDUNITDIR)/wldm.service

install-dbus-policy:
	install -d $(DESTDIR)$(DBUSSYSTEMPOLICYDIR)
	sed $(addprefix -e ,$(SUBST)) $(DBUS_POLICY_TEMPLATE) > $(DESTDIR)$(DBUSSYSTEMPOLICYDIR)/wldm-dbus.conf
	chmod 0644 $(DESTDIR)$(DBUSSYSTEMPOLICYDIR)/wldm-dbus.conf

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/wldm
	rm -rf $(DESTDIR)$(WLDM_DATADIR)
	rm -f $(DESTDIR)$(SYSCONFDIR)/wldm.ini
	rm -f $(DESTDIR)$(SYSTEMDUNITDIR)/wldm.service
	rm -f $(DESTDIR)$(DBUSSYSTEMPOLICYDIR)/wldm-dbus.conf
