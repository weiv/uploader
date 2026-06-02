# Routing the Uploader through a Cloudflare Tunnel

The uploader binds to `127.0.0.1:8000` and has **no authentication of its own**.
A Cloudflare Tunnel does two jobs for it:

1. **Exposes** the server to the internet without opening any inbound port on the
   Pi or your router (the tunnel makes an *outbound* connection to Cloudflare).
2. **Authenticates** every request via Cloudflare Access *before* it ever reaches
   the Pi.

> **Read this first — security model:**
> The app trusts that anyone who reaches it is already authenticated. If you route
> a tunnel to it **without** a Cloudflare Access policy in front, you have published
> an open, anonymous upload/download endpoint to the entire internet. **Steps 1–6
> are not enough on their own. Step 7 (Access) is mandatory.** Verify it (Step 9)
> before you consider the box exposed.

---

## Prerequisites

- A domain managed in Cloudflare (using Cloudflare's nameservers). Free plan is fine.
- The uploader installed and running on the Pi (`sudo ./setup.sh`; confirm with
  `systemctl status uploader` and `curl -sf http://127.0.0.1:8000/ >/dev/null && echo ok`).
- A way to reach the Cloudflare dashboard / Zero Trust dashboard.

Throughout, replace `files.example.com` with the hostname you want, and `example.com`
with your domain.

---

## Step 1: Install `cloudflared` on the Pi

Cloudflare publishes an `apt` repo with `arm64`/`armhf` builds for Raspberry Pi OS:

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
cloudflared --version
```

(Alternatively, download the `.deb` for your architecture from the cloudflared
GitHub releases and `sudo dpkg -i` it.)

---

## Step 2: Authenticate cloudflared to your Cloudflare account

```bash
cloudflared tunnel login
```

This prints a URL. Open it in a browser, log in, and pick the zone (`example.com`).
It writes a certificate to `~/.cloudflared/cert.pem` for the user that ran it. This
`cert.pem` is the account/zone credential used for **management** calls — creating
the tunnel (Step 3) and the DNS route (Step 5) need it. It is *not* used to run the
tunnel (Step 6 runs off the per-tunnel `<UUID>.json` instead), which is why the
running service doesn't need it.

> Decide **which user owns the tunnel**. Running everything as your admin user
> (`weiv`) and then installing the service (Step 6) is simplest. The credentials
> live under that user's `~/.cloudflared/`.

---

## Step 3: Create a named tunnel

```bash
cloudflared tunnel create uploader
```

This creates the tunnel and writes its credentials file:
`~/.cloudflared/<TUNNEL-UUID>.json`. Note the UUID it prints — you'll reference it
in the config. List tunnels any time with `cloudflared tunnel list`.

---

## Step 4: Write the tunnel config

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: uploader
credentials-file: /home/weiv/.cloudflared/<TUNNEL-UUID>.json

ingress:
  - hostname: files.example.com
    service: http://127.0.0.1:8000
  # Catch-all: everything else gets a 404. Required as the last rule.
  - service: http_status:404
```

Notes:
- `service` points at the uploader's local bind address. If you changed `PORT`,
  match it here.
- The final catch-all rule is required — cloudflared refuses configs without one.
- Use the absolute path to the credentials file (the systemd service in Step 6
  runs without your shell's `~` expansion guarantees).

Validate the config:

```bash
cloudflared tunnel ingress validate
cloudflared tunnel ingress rule https://files.example.com   # shows which rule matches
```

---

## Step 5: Point DNS at the tunnel

```bash
cloudflared tunnel route dns uploader files.example.com
```

This creates a proxied `CNAME` (`files.example.com → <TUNNEL-UUID>.cfargotunnel.com`)
in your Cloudflare DNS. It must be **proxied** (orange cloud) — that's what routes
traffic through Cloudflare and lets Access protect it.

If a record for that hostname already exists, the command fails with
`code: 1003 ... record with that host already exists`. Either replace it in place:

```bash
cloudflared tunnel route dns --overwrite-dns uploader files.example.com
```

or delete the existing record first / add the route in the dashboard instead.

---

## Step 6: Run the tunnel as a service

Test it in the foreground first:

```bash
cloudflared tunnel run uploader
```

In another terminal (or from your laptop) hit `https://files.example.com` — you
should reach the uploader (anonymously, for now; we lock it down next). `Ctrl-C`
to stop, then install it as a boot service:

```bash
# Pass the config path explicitly — see the warning below.
sudo cloudflared --config /home/weiv/.cloudflared/config.yml service install
sudo systemctl enable --now cloudflared
systemctl status cloudflared
```

> **Why the explicit `--config`:** the installed systemd service always runs as
> **root**, and `sudo cloudflared service install` (with no `--config`) looks in
> `/root/.cloudflared/` — *not* the `~/.cloudflared/` where you authenticated and
> created the tunnel. Without the path it'll fail to find your `config.yml`/
> credentials (or silently run with the wrong ones). Point it at the real config
> file (root can read it); the `credentials-file:` inside that config must also be
> an absolute path (Step 4) for the same reason. Confirm it picked up the right
> config with `journalctl -u cloudflared -n 50`.
>
> *(Alternative: a dashboard-managed "remote" tunnel is installed with a token —
> `sudo cloudflared service install <TOKEN>` — and stores its config in Cloudflare
> instead of `config.yml`. That's a different setup than the local-config flow used
> here; don't mix the two.)*

---

## Step 7: Protect it with Cloudflare Access (MANDATORY)

This is the authentication layer. Do **not** skip it.

In the **Zero Trust dashboard** (one-time: enable Zero Trust for your account, free
tier covers up to 50 users):

1. **Access → Applications → Add an application → Self-hosted.**
2. **Application domain:** `files.example.com` (path left blank to cover the whole site).
3. **Session duration:** your choice (e.g. 24h).
4. Add a **policy**:
   - **Action:** Allow.
   - **Include:** the rule that identifies you — e.g. *Emails* = `you@example.com`,
     or *Emails ending in* = `@example.com`, or a specific identity provider group.
5. Pick a **login method** (the default one-time-PIN-to-email works with no IdP setup;
   you can add Google/GitHub/etc. under **Settings → Authentication**).
6. Save.

Now every request to `files.example.com` must pass Access before Cloudflare forwards
it to the tunnel. Unauthenticated visitors get Cloudflare's login screen, never the Pi.

### Optional but recommended: defense in depth

- **Service tokens / `cloudflared access`** if you want scripted (non-browser) uploads:
  create a service token in Access and send the `CF-Access-Client-Id` /
  `CF-Access-Client-Secret` headers, or use `cloudflared access curl`.
- **WAF / rate limiting** on the zone to blunt abuse.
- Keep the app bound to `127.0.0.1` (it already is) so the only path in is the tunnel.

---

## Step 8: Large uploads

The uploader streams to disk and handles large files fine, but two limits sit in
front of it:

- **Cloudflare request body size.** The Free/Pro plans cap the upload body
  (100 MB on Free at time of writing). Larger files need a higher-tier plan or an
  alternative path. Check your plan's current limit before relying on big uploads.
- **Timeouts.** Very slow/large transfers can hit edge timeouts. If you see uploads
  failing partway, that's usually the edge, not the Pi (the partial `.part` file is
  cleaned up automatically).

If you must move files larger than the plan limit, copy them over SSH/`scp` directly
to `/srv/uploader/files` instead — `weiv` can write there (group `uploader-admin`,
setgid dir).

---

## Step 9: Verify

```bash
# On the Pi: both services healthy
systemctl status uploader cloudflared --no-pager

# From a machine that is NOT logged into Access. Look at the status line AND the
# location header — Access should bounce you to its own login domain:
curl -sI https://files.example.com | grep -i -E '^HTTP/|^location:'

# After logging in via the browser: the uploader UI loads, upload + download work.
```

**Pass:** a `30x` redirect whose `location:` points at a `*.cloudflareaccess.com`
(or your team's `*.cloudflareaccess.com`) login URL. A bare `401` with a
`WWW-Authenticate` header is *also* protected — that's what Access returns to
non-browser clients when **Managed OAuth** is enabled on the app, so don't mistake
it for a failure.

**Fail (open endpoint — STOP):** a `200` response, or the uploader's HTML markup,
**before** you have logged in. That means Access is not in front of the app. Recheck
Step 5 (the DNS record must be **proxied** / orange-cloud) and Step 7 (the Access
application's domain must match the hostname exactly). Do not leave it exposed in
this state.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `https://files.example.com` → 502/error | Tunnel up but uploader down, or wrong `service:` port. Check `systemctl status uploader` and the port in `config.yml`. |
| Tunnel won't start, "credentials" error | `credentials-file` path wrong, or service running as a user without `~/.cloudflared/`. Check `journalctl -u cloudflared`. |
| Reaches the app with no login prompt | DNS record not proxied (grey cloud), or Access policy hostname doesn't match. |
| 404 for everything | Hostname in `config.yml` doesn't match the request; fell through to the `http_status:404` catch-all. |
| Uploads fail near a size threshold | Cloudflare plan body-size limit (Step 8). |

---

## Quick reference

```bash
cloudflared tunnel login                         # one-time auth
cloudflared tunnel create uploader               # make the tunnel
# edit ~/.cloudflared/config.yml (ingress -> http://127.0.0.1:8000)
cloudflared tunnel route dns uploader files.example.com   # add --overwrite-dns if it exists
cloudflared tunnel ingress validate
sudo cloudflared --config /home/weiv/.cloudflared/config.yml service install
sudo systemctl enable --now cloudflared
# then: Zero Trust dashboard -> Access -> Application + Allow policy  (MANDATORY)
```
