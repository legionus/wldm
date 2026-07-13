# Installation Guide

## System Requirements

- Python 3.8+
- GTK 4 development headers
- Linux PAM development files
- Wayland compositor (default: cage)

## Ubuntu/Debian

```bash
sudo apt-get install python3-dev libgtk-4-dev libpam-dev libglib2.0-dev \
  libgobject-introspection-1.0-dev gir1.2-gtk-4 cage
```

## Fedora/RHEL

```bash
sudo dnf install python3-devel gtk4-devel pam-devel gobject-introspection-devel cage
```

## Arch

```bash
sudo pacman -S python gtk4 pam gobject-introspection cage
```

## Installation

```bash
# Check dependencies
make check-deps

# Build and install
sudo make install

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable --now wldm
```