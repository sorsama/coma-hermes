# coma-hermes

The Hermes Agent plugin that lets the [Coma](https://github.com/sorsama/coma) phone app reach your
Hermes from anywhere.

It turns on Hermes' API server and links Hermes to a Coma relay: Hermes dials out and holds a
connection open, and the phone talks to Hermes through it. Nothing has to be opened on your router,
and your Hermes API key never leaves your machine.

## Install

```
hermes plugins install sorsama/coma-hermes --enable
```

Then restart Hermes.

## Pair

Pick whichever is handy. All three show the same code.

| Where | How |
| --- | --- |
| Hermes Desktop | Switch on **Coma** in Capabilities → Plugins, open **Coma** in the sidebar, press **Start pairing** |
| Terminal | `hermes coma` |
| Any Hermes chat | `/coma`, and `/coma stop` to drop the link |

In the Coma app, tap **Scan pairing code** and point it at the QR. On the same Wi-Fi the app also
lists the machine by name; pick it and type the six-digit code shown beside the QR.

The first pairing adds `API_SERVER_ENABLED` and a random `API_SERVER_KEY` to Hermes' `.env` if they
are missing, and asks you to restart Hermes once so the API server starts. After that the gateway
links to the relay by itself every time Hermes starts, so the phone can reach it whenever Hermes is
running.

## Skip the relay on your own Wi-Fi

Hermes' API server listens on the computer itself unless told otherwise. Add this to Hermes' `.env`
and restart Hermes, and phones on the same network can talk to it directly:

```
API_SERVER_HOST=0.0.0.0
```

Then the app offers **Direct on this Wi-Fi** when you pair with the six-digit code, and it also finds
the API server by itself even without pairing running (you type the `API_SERVER_KEY`). A direct
connection only works on that network; pair through the relay to reach Hermes from anywhere.

## The relay

By default the plugin uses the hosted relay at `https://coma-relay.zyphite.com`. Its free plan pairs
one phone with each Hermes; Plus, a subscription in the Coma app, pairs any number.

To point the plugin at a different relay, use either of:

```
hermes coma --relay https://your.relay
```

or **Use my own relay** on the Coma page in Hermes Desktop. The `COMA_RELAY_URL` environment
variable overrides both. Phones paired through the old relay have to pair again.

## What to know

- **A pairing code admits one phone** and stops working after ten minutes. Don't share it: a paired
  phone can run commands through your Hermes.
- **Unpairing a phone** is on the Coma page in Hermes Desktop.
- **The relay can see what passes through it.** Traffic is encrypted to the relay and from the relay,
  not end to end. If that matters to you, skip the relay: in the app, type the address and API key
  of a host on your own network, VPN or HTTPS proxy.
- **Finding the machine on Wi-Fi** uses a UDP broadcast on port 48642 and a short HTTP exchange on
  the local network. The pairing token is only returned for the right code, and the code is redrawn
  after five wrong tries, but that exchange is not encrypted: on a network you don't trust, use the QR.
- **Direct pairing hands over the API key.** With `API_SERVER_HOST=0.0.0.0`, the right six-digit
  code returns the API server's port and key along with the relay offer. The QR and `/coma` replies
  never carry the key.
- **What the app reads and changes.** The plugin adds routes under `/api/coma/` to Hermes' API
  server, behind its usual API key: the skills list with on/off switches, plugins, MCP connectors,
  the context window of a model, and Hermes Desktop's settings (read and written in `config.yaml`;
  values of keys, tokens and passwords are masked). They appear the next time Hermes starts.
- **Settings** live in `coma-relay.json` in Hermes' home folder: the relay address and this Hermes'
  id and secret there. Delete it to start over.

## Layout

```
plugin.yaml              plugin manifest
__init__.py              hermes coma, /coma, and the gateway's automatic link
pairing.py               the link to the relay, LAN pairing and QR, shared by every surface
api_routes.py            the /api/coma/ routes the app reads: skills, plugins, connectors, settings
qr.py                    the QR encoder, so pairing needs no extra package
dashboard/plugin_api.py  backend for the Desktop page (/api/plugins/coma)
desktop/plugin.js        the Coma page in Hermes Desktop
```

MIT licensed.
