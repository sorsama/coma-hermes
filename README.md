# coma-hermes

The Hermes Agent plugin that pairs the [Coma](https://github.com/sorsama/coma) phone app with your Hermes.

It turns on Hermes' API server, opens a free Cloudflare quick tunnel to it, and shows a QR code the
app scans. Nothing is stored in between: the phone talks to your Hermes through the tunnel.

## Install

```
hermes plugins install sorsama/coma-hermes --enable --force
```

`--force` is needed because Hermes' install-time security scan blocks this plugin by default. The
scan flags every plugin that starts a tunnel, and this one does exactly that: it runs `cloudflared`
to make your Hermes reachable from your phone. Run the command without `--force` first to read the
findings, and look at `pairing.py` (it is short) before you override them.

Then restart Hermes. You also need [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
on the machine that runs Hermes. No Cloudflare account is needed.

## Pair

Pick whichever is handy. All three show the same code.

| Where | How |
| --- | --- |
| Hermes Desktop | Switch on **Coma** in Capabilities → Plugins, open **Coma** in the sidebar, press **Start pairing** |
| Terminal | `hermes coma` (runs until Ctrl+C) |
| Any Hermes chat | `/coma`, and `/coma stop` to close the tunnel |

In the Coma app, tap **Scan pairing code** and point it at the QR. On the same Wi-Fi the app also
lists the machine by name; pick it and type the six-digit code shown beside the QR.

The first pairing adds `API_SERVER_ENABLED` and a random `API_SERVER_KEY` to Hermes' `.env` if they
are missing, and asks you to restart Hermes once so the API server starts.

## What to know

- **The code is a key to your machine.** The QR holds the tunnel address and the API key, which
  lets its holder run commands through Hermes. Don't share it or show it on a screen share.
- **The address changes.** A quick tunnel gets a new address every time pairing starts. Scan again
  and the app updates the host it already has.
- **Quick tunnels are best-effort.** Cloudflare gives them no uptime guarantee and limits how often
  new ones can be opened. For something permanent, put Hermes behind a named tunnel or your own
  HTTPS proxy and enter that address in the app by hand.
- **Finding the machine on Wi-Fi** uses a UDP broadcast on port 48642 and a short HTTP exchange on
  the local network. The address and key are only returned for the right code, and the code is
  redrawn after five wrong tries, but that exchange is not encrypted: on a network you don't trust,
  use the QR.

## Layout

```
plugin.yaml              plugin manifest
__init__.py              hermes coma and /coma
pairing.py               tunnel, LAN pairing and QR, shared by every surface
dashboard/plugin_api.py  backend for the Desktop page (/api/plugins/coma)
desktop/plugin.js        the Coma page in Hermes Desktop
```

MIT licensed.
