# Homepage Editor

A web page to edit the config of [Homepage](https://gethomepage.dev): services, bookmarks,
layout and tabs, look and background, and any config file directly.

Before every save or restore, **all** config files are backed up into a folder named with the
date and time (e.g. `backups/2026-10-15_22-45-03`). The Backups page shows what changed since
each one and restores any of them with one click.

> Unofficial tool. Not made by or affiliated with the Homepage project.
> It only reads and writes Homepage's YAML/CSS/JS config files.

Version 1.0.0 · MIT licence · runs on **amd64** and **arm64** (e.g. Raspberry Pi 3/4/5 with a
64-bit system; 32-bit ARM is not supported)

## Install

Pick one of three ways. All need Docker with the Compose plugin, on the same machine as
Homepage's config folder.

| | Ready image + setup.sh | Ready image, by hand | Build from source |
|---|---|---|---|
| You need | the repository | only `docker-compose.yml` and `.env` | the repository |
| Settings | asked by `setup.sh` | you edit `.env` | asked by `setup.sh`, or by hand |
| Updates | `docker compose pull`, or Watchtower | same | `git pull` + rebuild |

### 1. Ready image with setup.sh (easiest)

```bash
git clone https://github.com/bottega-lab/homepage-editor.git
cd homepage-editor
chmod +x setup.sh
./setup.sh
```

`setup.sh` asks the questions, suggests answers it can detect, and writes `.env`:

1. **Homepage's config folder.** Found for you when Homepage runs in Docker on this server
   (from the container's config mount, also its port). Otherwise it searches the usual folders
   and only suggests folders that really look like Homepage's config. You always confirm.
2. **Backups**: where to keep them and how many.
3. **Homepage's address and port**, for the Open Homepage button.
4. **How to get the app** (ready image or build here), the stack name, the editor's port,
   and the **login**: a login page (recommended), or none, only when something else already
   protects the editor (Authelia, Authentik, an NPM access list, a VPN).
5. **Owner of the config files, time zone, Docker network**: detected, press Enter to accept.
   Time zone: `?` shows region and city lists, or type part of a city to search.

Run it again any time to change something: your current values are the defaults.

### 2. Ready image, by hand (without setup.sh)

```bash
mkdir homepage-editor && cd homepage-editor
curl -fsSLO https://raw.githubusercontent.com/bottega-lab/homepage-editor/main/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/bottega-lab/homepage-editor/main/.env.example -o .env
nano .env                    # fill in at least HOMEPAGE_CONFIG and EDITOR_PASSWORD
docker compose up -d
```

Only two settings are required; see the table below for the rest. The editor creates its
`backups` and `data` folders next to `docker-compose.yml` and gives them to `PUID:PGID` itself,
so there is nothing else to prepare. Check the start with `docker logs homepage-editor`.

**Values you may need to look up**

- Homepage's config folder:
  `docker inspect homepage --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'`
  (the line ending in `/app/config`; use your container's name instead of `homepage`)
- Homepage's port: `docker port homepage` (the number after `0.0.0.0:`)
- PUID / PGID: `ls -ln /path/to/homepage/config` (the two numbers after the permissions)
- A free Docker network: `docker network inspect $(docker network ls -q) --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}'`
  lists the ones in use.

### 3. Build from the source

Same as 1, and choose **2 = build it here** in `setup.sh`. By hand: in `.env`, use the line
`COMPOSE_FILE='docker-compose.yml:docker-compose.build.yml'` and start with
`docker compose up -d --build`. Everything in the image is then built from the files in
`editor/`, which you can read first.

## Settings (`.env`)

| Setting | Required | Example / default | What it is |
|---|---|---|---|
| `HOMEPAGE_CONFIG` | **yes** | `/opt/homepage/config` | Homepage's config folder on this server |
| `EDITOR_PASSWORD` | **yes** (with login) | | Password for the login page, at least 8 characters |
| `PUID`, `PGID` | | `1000` | Owner of Homepage's config files; the editor runs as this user |
| `EDITOR_USER` | | `admin` | User name for the login page |
| `AUTH_MODE` | | `login` | `login`, or `off` = no login (only behind other protection) |
| `EDITOR_PORT` | | `5005` | Port of the editor page |
| `HOMEPAGE_PROTOCOL` / `HOST` / `PORT` | | `http` / `192.168.1.10` / `3000` | Where Open Homepage goes; port may be empty |
| `BACKUP_PATH` | | `./backups` | Where the dated backups go (keep it outside Homepage's config) |
| `DATA_PATH` | | `./data` | The editor's own settings: login, passkeys, logo |
| `BACKUP_KEEP` | | `100` | How many backups to keep, `0` = all |
| `TZ` | | `UTC` | Time zone for backup dates, e.g. `Europe/Amsterdam` |
| `STACK_NAME` | | `homepage-editor` | Name of the stack and container |
| `EDITOR_SUBNET` | | `172.31.250.0/24` | Docker network of the stack; change it if already used |
| `IMAGE` | | `ghcr.io/bottega-lab/homepage-editor:1` | The ready image; `:1` = all 1.x updates (safe), `:latest` = always the newest, `:1.0.0` = never changes |
| `COMPOSE_FILE` | | `docker-compose.yml` | Add `:docker-compose.build.yml` to build from source |
| `APP_DIR` | | | Only for tools that keep their own copy of the compose file (Dockhand) |

After changing `.env`: `docker compose up -d`.

## What setup.sh does (and does not)

It is a plain Bash script; read it before running it if you like. It:

- **asks questions** and writes **one file, `.env`**, in its own folder (an old `.env` is kept as `.env.bak`);
- **looks around read-only** to suggest answers: `docker ps` / `docker inspect` (Homepage's config
  folder and port), `docker network inspect` (free network), `ss` (free port), `ls`/`stat` (file
  owner), `timedatectl` (time zone), and, only if no Homepage container is found, a search for
  Homepage-like folders in your home folder, `/opt`, `/srv`, `/docker` and `/data`;
- checks whether the ready image exists (`docker manifest inspect`, which contacts the image registry);
- **creates the backups and data folders** and gives them to PUID:PGID, using `sudo` only for that
  and only if it is needed;
- at the end, **only if you say yes**, starts the editor (`docker compose pull` / `up`).

It does **not** change Homepage's files or container, install anything, or send any data anywhere.

## Login

- A normal login page, so password managers (Bitwarden, 1Password, the browser's own) can save
  and fill it.
- **Stay logged in** keeps you logged in on that browser for 30 days (changeable). Otherwise you
  are logged out after 60 minutes without activity.
- **Passkeys**: add one in Settings › Security. Browsers only allow passkeys on `https://` pages
  (or `http://localhost`), so open the editor through your reverse proxy with a domain and HTTPS.
- After 5 wrong passwords in a row, logging in from that address is blocked for 5 minutes.
- **Forgot your password?** Run `./setup.sh` and type a new one (or change `EDITOR_PASSWORD` in
  `.env` and run `docker compose up -d`).

## Settings in the app

Click your initial (top right) › **Settings**: appearance and logo, login and passkeys,
Homepage link, backups, About. Values set there win over `.env`, until you change the same
value in `.env` / `setup.sh`. **Feedback** (top) opens GitHub or Discord with your message filled
in; the editor itself sends nothing.

## Update

- Ready image: `docker compose pull && docker compose up -d`, or let Watchtower do it.
- Built from source: `git pull && docker compose up -d --build`.

## Good to know

- Saving from the forms rewrites the file neatly and removes `#` comments. The Files page keeps
  exactly what you type.
- The container starts as root only to give its `backups` and `data` folders to PUID:PGID, then
  runs as PUID:PGID. Homepage's config folder is never changed; if PUID:PGID cannot write it, the
  log says so.
- **Keep it on your own network**, or behind your reverse proxy with HTTPS: it shows the API keys
  in your Homepage config.

## License

MIT, see [LICENSE](LICENSE).
