# QuakeAlert Server – Recovery Guide

Use this guide to **rebuild the server from scratch** (e.g. after a fresh clone or new VPS). These files are not in the repo and must be restored from your backup.

---

## 1. Files you must back up (keep them safe)

Back these up **before** you lose the server. Store them in a secure place (password manager, encrypted disk, or private backup repo).

| File | Purpose |
|------|--------|
| **`.env`** | MQTT, Ntfy, Telegram secrets. Copy from `.env.example` and fill in real values. |
| **`config/pwfile`** | Mosquitto user/password. Copy from `config/pwfile.example` and replace with your hashed passwords. |
| **`config/firebase-key.json`** | Firebase service account JSON for Ntfy (Play Store push). Download from Firebase Console. |
| **`nginx_quakealert.conf`** | Nginx server block for your domain. Use `nginx_quakealert.conf.example` in this repo as a template if you lost the original. |

---

## 2. Define the chat rate-limit zone (required for nginx)

The nginx config uses `limit_req zone=chat_limit`. You **must** define this zone somewhere that nginx loads in the `http` block (e.g. in `/etc/nginx/nginx.conf` or an included file).

Add this **once** inside the `http { ... }` block (not inside a `server` block):

```nginx
# Rate limit for chat (Socket.IO) – e.g. in /etc/nginx/nginx.conf inside http { }
limit_req_zone $binary_remote_addr zone=chat_limit:10m rate=10r/s;
```

Then reload nginx: `sudo nginx -t && sudo systemctl reload nginx`.

If this zone is missing, nginx will fail to start or fail to load the QuakeAlert server block.

---

## 3. Recovery steps (fresh clone or new server)

### 3.1 Clone and enter the repo

```bash
git clone https://github.com/banana-pixel/QuakeAlert-Server.git
cd QuakeAlert-Server
```

### 3.2 Restore secrets and config

- Copy your backed-up **`.env`** into the repo root. If you don’t have it, copy `.env.example` to `.env` and fill in MQTT, Ntfy, and Telegram values.
- Copy **`config/pwfile`** (or create from `config/pwfile.example` and set Mosquitto passwords).
- Place **`config/firebase-key.json`** in the `config/` folder (required for Ntfy Firebase push).

### 3.3 Nginx

- Copy your backed-up **`nginx_quakealert.conf`** to your nginx sites (e.g. `/etc/nginx/sites-available/` and symlink in `sites-enabled/`), **or** copy `nginx_quakealert.conf.example` to that path and rename it, then adjust `server_name` and SSL paths if your domain or Certbot paths differ.
- Ensure the **`limit_req_zone`** for `chat_limit` is defined in your main nginx config (see section 2).
- Run: `sudo nginx -t && sudo systemctl reload nginx`.

### 3.4 SSL (if new server)

If this is a new hostname or VPS, get a certificate:

```bash
sudo certbot --nginx -d quakealert.bananapixel.my.id
```

(Use your actual domain.) Certbot will update the SSL paths in your server block if you use its managed config.

### 3.5 Start the stack

```bash
docker compose up -d --build
```

Check that containers are up: `docker compose ps`. The report server responds at `http://127.0.0.1:5000/` (health check).

---

## 4. Quick checklist

- [ ] `.env` in repo root (from backup or `.env.example` filled in)
- [ ] `config/pwfile` (from backup or from `config/pwfile.example`)
- [ ] `config/firebase-key.json` in `config/`
- [ ] Nginx server block in place (from backup or `nginx_quakealert.conf.example`)
- [ ] `limit_req_zone chat_limit` defined in nginx `http` block
- [ ] SSL certificate (Certbot) if new server
- [ ] `docker compose up -d --build` run successfully

---

## 5. Optional: Back up the live nginx config into the repo

If you want the **exact** live config in the repo (e.g. for a different branch or backup), copy it to a name that is **not** in `.gitignore`, for example:

```bash
cp nginx_quakealert.conf nginx_quakealert.conf.backup
# Then commit nginx_quakealert.conf.backup (not ignored)
```

Do **not** commit `.env`, `config/pwfile`, or `config/firebase-key.json`; they contain secrets.
