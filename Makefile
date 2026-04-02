# SPDX-License-Identifier: GPL-2.0-or-later

PYTHON ?= python3
DESTDIR ?=
SYSCONFDIR ?= /etc
PREFIX ?= /usr
DATADIR ?= $(PREFIX)/share
BINDIR ?= $(PREFIX)/sbin
SYSTEMDUNITDIR ?= $(PREFIX)/lib/systemd/system
LOCALEDIR ?= $(PREFIX)/share/locale

WLDM_DATADIR := $(DATADIR)/wldm
CONFIG_TEMPLATE := config/wldm.ini.in
SERVICE_TEMPLATE := systemd/wldm.service.in

.PHONY: all install install-python install-data install-config install-systemd uninstall

all:

install: install-python install-data install-config install-systemd

install-python:
	$(PYTHON) -m pip install . --root $(DESTDIR) --no-deps --no-build-isolation

install-data:
	install -d $(DESTDIR)$(WLDM_DATADIR)/resources
	install -m 0644 resources/greeter.ui $(DESTDIR)$(WLDM_DATADIR)/resources/greeter.ui
	install -m 0644 resources/style.css $(DESTDIR)$(WLDM_DATADIR)/resources/style.css
	install -d $(DESTDIR)$(WLDM_DATADIR)/scripts
	install -m 0755 scripts/wayland-session $(DESTDIR)$(WLDM_DATADIR)/scripts/wayland-session

install-config:
	install -d $(DESTDIR)$(SYSCONFDIR)
	sed \
		-e 's|@datadir@|$(WLDM_DATADIR)|g' \
		-e 's|@localedir@|$(LOCALEDIR)|g' \
		$(CONFIG_TEMPLATE) > $(DESTDIR)$(SYSCONFDIR)/wldm.ini
	chmod 0644 $(DESTDIR)$(SYSCONFDIR)/wldm.ini

install-systemd:
	install -d $(DESTDIR)$(SYSTEMDUNITDIR)
	sed \
		-e 's|@bindir@|$(BINDIR)|g' \
		-e 's|@sysconfdir@|$(SYSCONFDIR)|g' \
		$(SERVICE_TEMPLATE) > $(DESTDIR)$(SYSTEMDUNITDIR)/wldm.service
	chmod 0644 $(DESTDIR)$(SYSTEMDUNITDIR)/wldm.service

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/wldm
	rm -rf $(DESTDIR)$(WLDM_DATADIR)
	rm -f $(DESTDIR)$(SYSCONFDIR)/wldm.ini
	rm -f $(DESTDIR)$(SYSTEMDUNITDIR)/wldm.service
