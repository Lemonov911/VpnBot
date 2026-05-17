# ops/

Server-side scripts that live on prod under `/opt/vpnbot/`. Committed here so
they survive accidental deletion (one-time outage 2026-05-17 lost both — see
[CLAUDE.md](../CLAUDE.md) «VPS Operations» note).

## Files

| File | Where on prod | Purpose |
|---|---|---|
| `deploy-bot.sh` | `/opt/vpnbot/deploy-bot.sh` (root:root, 0750) | CI deploys: pulls main, restarts `vpnbot`, healthchecks `/api/health` |
| `deploy-admin.sh` | `/opt/vpnbot/deploy-admin.sh` (root:root, 0750) | CI deploys: pulls main, `npm ci && npm run build` in `admin/`, restarts `vpnbot-admin`, healthchecks `:3001/admin/login` |

## How CI invokes them

`.github/workflows/deploy-bot.yml` and `deploy-admin.yml` SSH to prod as
the `deploy` user. The deploy SSH keys have **forced-command** restrictions
in `~/.deploy/.ssh/authorized_keys`:

```
command="sudo /opt/vpnbot/deploy-bot.sh",no-pty,no-port-forwarding ssh-ed25519 AAAA... bot-deploy
command="sudo /opt/vpnbot/deploy-admin.sh",no-pty,no-port-forwarding ssh-ed25519 AAAA... admin-deploy
```

So whatever the CI sends (`ssh ... true`), the server actually runs the
deploy script. `/etc/sudoers.d/deploy` whitelists only these two paths:

```
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl reload nginx, /opt/vpnbot/deploy-bot.sh, /opt/vpnbot/deploy-admin.sh
```

## Restore after loss

If `/opt/vpnbot/deploy-bot.sh` or `/opt/vpnbot/deploy-admin.sh` are missing:

```bash
# From this repo on local machine
scp ops/deploy-bot.sh ops/deploy-admin.sh root@151.243.113.31:/opt/vpnbot/
ssh root@151.243.113.31 'chown root:root /opt/vpnbot/deploy-*.sh && chmod 750 /opt/vpnbot/deploy-*.sh'
```

Then trigger CI to verify (push a no-op or `gh run rerun ...`).

## Shared lock

Both scripts `flock` on `/var/lock/vpnbot-deploy.lock` because both touch
`/opt/vpnbot/.git` (bot and admin live in the same working tree). Without
the lock, two concurrent CI runs would race on `git pull` and one would
fail with «failed to lock ref» or worse.

The CI workflow files also share a `concurrency: deploy-vpnbot-server`
group — the lock is the second layer of defence.
