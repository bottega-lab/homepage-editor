# Changelog

## 1.0.0
First public release.
- Web editor for Homepage (gethomepage.dev): services, bookmarks, layout and look, with a dated
  backup before every change and one-click restore.
- Own login page (password managers can fill it), "stay logged in", log out after inactivity,
  "log out everywhere else", 5-minute block after 5 wrong passwords, and passkeys on https:// pages.
  Login can be switched off when something else already protects the editor.
- Settings window: appearance (System / Light / Dark), your own logo, Homepage link, backups, About.
- Built-in Help page and a Feedback window (GitHub issue or Discord; nothing is sent by the app).
- First-run `setup.sh`: finds Homepage's config folder and port, time zone picker, free Docker subnet.
- Ready image for **amd64 and arm64** (Raspberry Pi 3/4/5 with a 64-bit system) on ghcr.io.
  Building from source stays possible (`docker-compose.build.yml`).
- The container gives its own `backups` and `data` folders to PUID:PGID at start, then runs as
  PUID:PGID. Homepage's config folder is never changed.
- Health check for Docker and monitoring tools.
