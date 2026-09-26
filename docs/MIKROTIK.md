# Example: MikroTik RouterOS

HomeIsland works with any router. This page shows one concrete setup on
MikroTik RouterOS 7. Adapt names and addresses; the examples use
`192.168.1.0/24`, router `192.168.1.1`, HomeIsland `192.168.1.10`.

HomeIsland never logs in to your router. Run these commands yourself in a
terminal (WinBox → New Terminal, or SSH).

## 1. Keep the HomeIsland address out of the DHCP pool

```
/ip pool print
/ip pool set [find name=dhcp] ranges=192.168.1.100-192.168.1.254
```

Alternatively, create a static lease for the server's MAC address:

```
/ip dhcp-server lease add address=192.168.1.10 mac-address=AA:BB:CC:DD:EE:FF comment=homeisland
```

(The MAC address above is a placeholder; see `/ip dhcp-server lease print`.)

## 2. Option A — clients use HomeIsland directly

```
/ip dhcp-server network print
/ip dhcp-server network set [find address=192.168.1.0/24] dns-server=192.168.1.10,192.168.1.1
```

The second entry (the router) is the fallback when HomeIsland is down. Add a
static entry on the router so the dashboard name resolves through the fallback
as well:

```
/ip dns static add name=home.home.arpa address=192.168.1.10 comment=homeisland
```

## 2. Option B — router forwards to HomeIsland

Clients keep using the router (`dns-server=192.168.1.1`); the router forwards
the local domain to HomeIsland and everything else upstream:

```
/ip dns set allow-remote-requests=yes servers=9.9.9.9,149.112.112.112
/ip dns static add name=home.arpa type=FWD forward-to=192.168.1.10 match-subdomain=yes comment=homeisland
/ip dhcp-server network set [find address=192.168.1.0/24] dns-server=192.168.1.1
```

This keeps Internet DNS working when HomeIsland is off, but Pi-hole only sees
the router as client and only filters what you forward to it. To send all
queries through Pi-hole with a fallback, use `servers=192.168.1.10,9.9.9.9`
instead (RouterOS tries servers in order).

Make sure the router's DNS service is not reachable from the WAN — the default
firewall rules drop input from the WAN interface list; verify with:

```
/ip firewall filter print where chain=input
```

## 3. Clients renew

Reconnect clients or wait for their lease to renew. Check on the router:

```
/ip dhcp-server lease print
/ip dns cache print where name~"home.arpa"
```

## Do not

- forward ports 53, 80 or 445 from the WAN to HomeIsland;
- disable the router's DHCP server in favour of Pi-hole's — HomeIsland is
  designed so the LAN survives without it.
