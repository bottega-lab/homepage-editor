"""Homepage Editor v1.1.1 - container start. SPDX-License-Identifier: MIT

Runs first, as root, like the linuxserver.io images do:
1. makes sure the editor's own folders (/backups and /data) belong to PUID:PGID,
   also when Docker created them (Docker creates missing folders as root);
2. checks that Homepage's config folder (/config) can be written by PUID:PGID (never changed);
3. drops root and starts the editor as PUID:PGID.
If the container is already started as a normal user (e.g. "user:" in compose), it only starts.
"""
import os
import sys

CMD = ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:8080", "app:app"]


def num(name, default=1000):
    try:
        value = int(os.environ.get(name, default))
        return value if value >= 0 else default
    except ValueError:
        print("WARNING: %s is not a number, using %d" % (name, default), flush=True)
        return default


def fix_owner(path, uid, gid):
    """Give the folder (and what is in it) to uid:gid. Only for the editor's own folders."""
    if not os.path.isdir(path):
        return
    st = os.stat(path)
    if (st.st_uid, st.st_gid) == (uid, gid):
        return
    print("Giving %s to %d:%d (it belonged to %d:%d)" % (path, uid, gid, st.st_uid, st.st_gid), flush=True)
    for root, dirs, files in os.walk(path):
        for name in [root] + [os.path.join(root, n) for n in dirs + files]:
            try:
                os.lchown(name, uid, gid)
            except OSError as exc:
                print("WARNING: could not change owner of %s: %s" % (name, exc), flush=True)


def can_write(path, uid, gid):
    st = os.stat(path)
    if uid == 0:
        return True
    if st.st_uid == uid:
        return bool(st.st_mode & 0o200)
    if st.st_gid == gid:
        return bool(st.st_mode & 0o020)
    return bool(st.st_mode & 0o002)


def main():
    if os.getuid() == 0:
        uid, gid = num("PUID"), num("PGID")
        for path in ("/backups", "/data"):
            fix_owner(path, uid, gid)
        if os.path.isdir("/config") and not can_write("/config", uid, gid):
            st = os.stat("/config")
            print("WARNING: Homepage's config folder belongs to %d:%d, but the editor runs as %d:%d, "
                  "so it cannot save. Set PUID/PGID in .env to the owner of that folder "
                  "(see: ls -ln of the folder)." % (st.st_uid, st.st_gid, uid, gid), flush=True)
        if uid != 0:
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
        os.environ["HOME"] = "/tmp"
    print("Starting Homepage Editor as %d:%d" % (os.getuid(), os.getgid()), flush=True)
    os.chdir("/app")
    os.execvp(CMD[0], CMD)


if __name__ == "__main__":
    sys.exit(main())
