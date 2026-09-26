# Networking and DNS

## Principle: HomeIsland is not required for the LAN to work

```
Internet
   │
Router ──── DHCP server for the LAN (stays here)
   │
Switch ─── phones, laptops, TVs, ...
   │
HomeIsland ─── DNS (Pi-hole), web services
```

The router keeps handing out IP addresses. HomeIsland only *offers* DNS and
services. If the HomeIsland box is switched off, every device still gets an
address, can still talk to other devices on the LAN, and can still reach the
Internet by IP. Pi-hole's DHCP server is disabled on purpose
(`FTLCONF_dhcp_active=false`).

## The local domain: `home.arpa`

HomeIsland names live under `home.arpa`, the domain reserved for home networks
by RFC 8375. `.local` is not used because it belongs to mDNS (Bonjour/Avahi) and
causes conflicts on Apple devices and Linux.

| Name | Service |
|---|---|
| `home.home.arpa` | dashboard |
| `status.home.arpa` | system status page |
| `pihole.home.arpa` | Pi-hole admin |
| `wiki.home.arpa` | Kiwix (module `kiwix`) |
| `library.home.arpa` | document library (module `library`) |
| `maps.home.arpa` | maps (module `maps`) |
| `files.home.arpa` | Samba shares (module `samba`) |
| `ha.home.arpa` | Home Assistant, port 8123 (module `homeassistant`) |
| `media.home.arpa` | Jellyfin (module `jellyfin`) |

`homeisland dns` lists the records that are active on your system. Pi-hole is
authoritative for the whole domain (`local=/home.arpa/`), so these names never
leave your network and resolve instantly during an Internet outage.

### Your own records

Add devices in `/etc/homeisland/dns-records.conf`:

```
192.168.1.1   router
192.168.1.20  printer
192.168.1.30  nas backup
```

and run `sudo homeisland apply`. Names without a dot get the HomeIsland domain
(`printer.home.arpa`). Pi-hole reloads without a restart.

## Configure the router

In the router's DHCP server settings, set the **DNS server** handed to clients
to the HomeIsland address (e.g. `192.168.1.10`). Leave the router as the DHCP
server and gateway. Clients pick up the change when they renew their lease
(reconnect Wi-Fi or wait for the lease time).

Typical locations of the setting:

- consumer routers: *LAN → DHCP server → Primary DNS*
- OpenWrt: *Network → Interfaces → LAN → DHCP Server → Advanced → DHCP-Options* `6,192.168.1.10`
- MikroTik RouterOS: `/ip dhcp-server network set dns-server=...` — see [MIKROTIK.md](MIKROTIK.md)
- pfSense/OPNsense: *Services → DHCP Server → LAN → DNS servers*

Do **not** point the router's *own* upstream DNS to HomeIsland unless you also
keep a fallback, otherwise the router itself loses DNS when HomeIsland is down.

HomeIsland never changes router settings itself.

### Verify

From a client:

```sh
nslookup wiki.home.arpa            # should answer with the HomeIsland address
nslookup example.com               # should work while online
```

On Windows, `ipconfig /all` shows the DNS server received via DHCP.

## What happens when DNS fails

If HomeIsland (or just Pi-hole) is down and clients only know HomeIsland as DNS
server:

- IP addresses, the gateway and direct IP connections keep working;
- **name resolution stops** — for Internet names and for `*.home.arpa` names;
- apps show "no Internet" although the WAN is fine.

Docker restarts a crashed Pi-hole within seconds, so this mainly matters when
the whole box is off or broken.

### Redundancy options

Pick one, from simplest to most robust:

1. **Router as second DNS server.** Hand out two DNS servers: HomeIsland first,
   router (or a public resolver) second. Clients fall back automatically.
   Trade-off: clients may sometimes use the second server even when HomeIsland
   is fine, bypassing ad blocking, and `*.home.arpa` names will not resolve via
   the router unless you add them there too (many routers support static DNS
   entries; add at least `home.home.arpa`).
2. **Router-side forwarding.** Clients use the router as DNS; the router
   forwards to HomeIsland and falls back to a public resolver. Only works with
   routers that support conditional forwarding or fallbacks (MikroTik,
   OpenWrt, pfSense). See [MIKROTIK.md](MIKROTIK.md).
3. **Second Pi-hole.** Run another Pi-hole on a different device, keep it in
   sync (e.g. with the Teleporter export in every HomeIsland backup), and hand
   out both. Most robust; also covers maintenance windows.

During a **WAN outage** none of this matters for local names: HomeIsland keeps
answering `*.home.arpa` from its own records. Internet names fail regardless,
because there is no Internet.

## Upstream DNS

Pi-hole forwards Internet names to `HOMEISLAND_DNS_UPSTREAMS` (default: Quad9,
two addresses). Use at least two. Change it in `/etc/homeisland/homeisland.env`
and run `sudo homeisland apply`.

## Ports and exposure

| Port | Service | Bound to |
|---|---|---|
| 53/udp, 53/tcp | Pi-hole DNS | LAN address |
| 80/tcp | dashboard and reverse proxy | LAN address |
| 445/tcp | Samba (module) | LAN address |
| 8123/tcp | Home Assistant (module, host networking) | all interfaces |

Ports are bound to `HOMEISLAND_BIND_IP` (default: the LAN address), not
`0.0.0.0`, because Docker's port publishing bypasses host firewalls such as
`ufw`. **Never forward any of these ports from the router to the Internet.**
For remote access use a VPN into your LAN (WireGuard on the router is ideal).

## IPv6

HomeIsland publishes services on IPv4. If your router advertises an IPv6 DNS
server via RA/DHCPv6, clients may prefer it and bypass Pi-hole. Either disable
IPv6 DNS advertisement on the router or make sure the IPv6 DNS server also
forwards `home.arpa` to HomeIsland.
