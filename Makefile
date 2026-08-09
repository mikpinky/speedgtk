APP_ID := io.github.speedgtk.SpeedGTK
APP_NAME := speedgtk
PREFIX ?= /usr/local
DESTDIR ?=
DATADIR := $(PREFIX)/share/$(APP_NAME)
BINDIR := $(PREFIX)/bin
APPLICATIONSDIR := $(PREFIX)/share/applications
ICONDIR := $(PREFIX)/share/icons/hicolor/scalable/apps
USER_CONFIG_DIR ?= $(or $(XDG_CONFIG_HOME),$(HOME)/.config)

.PHONY: all install install-user uninstall uninstall-user check test

all:
	@echo "Nothing to build: SpeedGTK is a Python application."

check:
	python3 -m py_compile speedgtk.py
	python3 -m unittest discover -s tests

test:
	python3 -m unittest discover -s tests -v

install: check
	install -d "$(DESTDIR)$(DATADIR)/po" "$(DESTDIR)$(BINDIR)" \
		"$(DESTDIR)$(APPLICATIONSDIR)" "$(DESTDIR)$(ICONDIR)"
	install -m 755 speedgtk.py "$(DESTDIR)$(DATADIR)/speedgtk.py"
	install -m 644 po/*.po "$(DESTDIR)$(DATADIR)/po/"
	sed 's|@DATADIR@|$(DATADIR)|g' scripts/speedgtk > "$(DESTDIR)$(BINDIR)/$(APP_NAME)"
	chmod 755 "$(DESTDIR)$(BINDIR)/$(APP_NAME)"
	install -m 644 data/$(APP_ID).desktop "$(DESTDIR)$(APPLICATIONSDIR)/$(APP_ID).desktop"
	install -m 644 data/icons/hicolor/scalable/apps/$(APP_ID).svg "$(DESTDIR)$(ICONDIR)/$(APP_ID).svg"

install-user:
	$(MAKE) install PREFIX="$(HOME)/.local"
	@command -v update-desktop-database >/dev/null && update-desktop-database "$(HOME)/.local/share/applications" || true
	@command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$(HOME)/.local/share/icons/hicolor" || true
	@echo "Installed. If $(HOME)/.local/bin is not in PATH, log out and back in or add it to PATH."

uninstall:
	rm -f "$(DESTDIR)$(BINDIR)/$(APP_NAME)" \
		"$(DESTDIR)$(APPLICATIONSDIR)/$(APP_ID).desktop" \
		"$(DESTDIR)$(ICONDIR)/$(APP_ID).svg"
	rm -rf "$(DESTDIR)$(DATADIR)"
	@if [ -n "$(SUDO_USER)" ]; then \
		user_home="$$(getent passwd "$(SUDO_USER)" | cut -d: -f6)"; \
		settings="$$user_home/.config/speedgtk/settings.json"; \
		if [ -f "$$settings" ]; then \
			sed -i 's/"ookla_terms_accepted"[[:space:]]*:[[:space:]]*true/"ookla_terms_accepted": false/' "$$settings"; \
		fi; \
	fi

uninstall-user:
	$(MAKE) uninstall PREFIX="$(HOME)/.local"
	rm -rf "$(USER_CONFIG_DIR)/speedgtk"
	@command -v update-desktop-database >/dev/null && update-desktop-database "$(HOME)/.local/share/applications" || true
	@command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$(HOME)/.local/share/icons/hicolor" || true
