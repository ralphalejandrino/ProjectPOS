# TarsierPOS — Gold Copy
Version: 1.0
Last updated: 2026-03-18

## Contents
- canteen/          Django backend app
- pos_config/       Django configuration
- frontend/public/  Vanilla JS PWA frontend
- wheels/           Vendored Python packages (offline install)
- install.sh        One-command installer
- uninstall.sh      Full wipe script
- build-image.sh    Builds deployment tarball
- health_check.sh   Systemd health watchdog
- backup_db.sh      SQLite backup rotation
- *.service         Systemd service files

## Deploy
sudo bash install.sh

## Wipe
sudo bash uninstall.sh

## Build USB Image
bash build-image.sh
