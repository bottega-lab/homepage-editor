# Changelog

## 1.1.0
- **Docker network**: by default Docker now chooses the stack's network, so it can no longer clash
  with an existing one. A fixed network is still possible (`EDITOR_SUBNET` +
  `docker-compose.subnet.yml`); setup.sh suggests one that overlaps no Docker network and none of
  the server's own networks (LAN, VLANs, VPN), or `auto`. It now also catches partial overlaps,
  e.g. a /24 inside a used /16.
- **Image version**: the default is now `:latest`. setup.sh, `.env.example` and the README explain
  the other choices: `:1` (all 1.x updates, never 2.0 by surprise) or an exact version.
- **Password**: when switching from "no login" to a login page, setup.sh no longer keeps a password
  it made silently and you have never seen: press Enter to get a new one shown on screen, or type
  your own. A password you set or saw earlier is still kept ("Enter = keep current").
- **Homepage port**: when you type another config folder than the one found (e.g. a copy),
  setup.sh still offers the port of your Homepage container instead of 3000.
- README / `.env.example`: warning that `STACK_NAME` and `EDITOR_PORT` must be unique on the server.

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
