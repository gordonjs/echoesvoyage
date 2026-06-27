#!/usr/bin/env python3
"""
rtk_bridge.py  -  NMEA <-> WebSocket + NTRIP correction bridge for Parcel RTK.

What it does
------------
1. Reads NMEA from your SparkFun RTK receiver (Bluetooth COM port, USB COM
   port, or TCP) and relays it to the web app over a WebSocket
   (ws://localhost:8765). In the app pick source = "Bridge (WS)".

2. (Option B) Optionally runs an NTRIP client: it pulls RTK correction data
   (RTCM) from a caster over THIS computer's internet (e.g. Starlink) and
   feeds it straight into the receiver over the same serial/Bluetooth link.
   It also sends your live position (GGA) back up to the caster, which
   network/VRS casters such as PointPerfect Flex require. The receiver then
   computes an RTK Fixed solution and the app shows centimetre accuracy.

   With this, the laptop does everything - the receiver needs no WiFi and no
   hotspot. (On the SparkFun Surveyor, corrections are injected over the
   Bluetooth serial link, so pair the receiver over Bluetooth for Option B.)

Dependency: `pyserial`, only for serial/Bluetooth ports:  pip install pyserial
(TCP and test sources need only the Python standard library.)

Examples
--------
List serial / Bluetooth COM ports:
    python rtk_bridge.py --list

Just relay NMEA (no corrections), Bluetooth COM port on Windows:
    python rtk_bridge.py --source serial:COM5:115200

Option B - relay NMEA AND feed corrections from an NTRIP caster:
    python rtk_bridge.py --source serial:COM5:115200 \
        --ntrip ntrip://USER:PASS@ppntrip.services.u-blox.com:2102/MOUNTPOINT

Free community caster example (needs a base near you):
    python rtk_bridge.py --source serial:COM5:115200 \
        --ntrip ntrip://you@example.com:none@rtk2go.com:2101/MOUNTPOINT

Synthetic data to test the app end to end (no hardware):
    python rtk_bridge.py --source test
"""
import argparse, base64, hashlib, socket, struct, sys, threading, time

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# --------------------------------------------------------------------------
# Minimal WebSocket server (stdlib only) - text frames, server->client only
# --------------------------------------------------------------------------
class WSHub:
    def __init__(self, host="0.0.0.0", port=8765):
        self.host, self.port = host, port
        self.clients = []
        self.lock = threading.Lock()

    def start(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(8)
        print(f"[ws] listening on ws://localhost:{self.port}")
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=self._handshake, args=(conn, addr), daemon=True).start()

    def _handshake(self, conn, addr):
        try:
            data = conn.recv(4096).decode("latin-1")
            key = None
            for line in data.split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            if not key:
                conn.close(); return
            accept = base64.b64encode(
                hashlib.sha1((key + WS_MAGIC).encode()).digest()).decode()
            conn.send((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            with self.lock:
                self.clients.append(conn)
            print(f"[ws] client connected {addr} ({len(self.clients)} total)")
            while True:
                if not conn.recv(1024):
                    break
        except Exception:
            pass
        finally:
            self._drop(conn)

    def _drop(self, conn):
        with self.lock:
            if conn in self.clients:
                self.clients.remove(conn)
                print(f"[ws] client gone ({len(self.clients)} total)")
        try: conn.close()
        except Exception: pass

    @staticmethod
    def _frame(text):
        payload = text.encode("utf-8")
        n = len(payload)
        hdr = bytearray([0x81])
        if n < 126:
            hdr.append(n)
        elif n < 65536:
            hdr.append(126); hdr += struct.pack(">H", n)
        else:
            hdr.append(127); hdr += struct.pack(">Q", n)
        return bytes(hdr) + payload

    def broadcast(self, text):
        frame = self._frame(text)
        with self.lock:
            dead = []
            for c in self.clients:
                try: c.sendall(frame)
                except Exception: dead.append(c)
        for c in dead:
            self._drop(c)


# --------------------------------------------------------------------------
# Shared state: latest GGA from the receiver (for the NTRIP caster)
# --------------------------------------------------------------------------
class State:
    def __init__(self):
        self.gga = None          # latest $..GGA line (str, no trailing newline)
        self.lock = threading.Lock()
    def set_gga(self, line):
        with self.lock:
            self.gga = line
    def get_gga(self):
        with self.lock:
            return self.gga


# --------------------------------------------------------------------------
# Serial source (bidirectional): reads NMEA, exposes a locked writer for RTCM
# --------------------------------------------------------------------------
class SerialSource:
    def __init__(self, spec):
        try:
            import serial  # pyserial
        except ImportError:
            sys.exit("pyserial not installed. Run:  pip install pyserial")
        self._serial = serial
        _, port, *rest = spec.split(":")
        self.port = port
        self.baud = int(rest[0]) if rest else 115200
        self.ser = None
        self.wlock = threading.Lock()
        self._open()

    def _open(self):
        """Open the port, retrying forever so a Bluetooth dropout self-heals."""
        first = True
        while True:
            try:
                self.ser = self._serial.Serial(self.port, self.baud, timeout=1)
                print(f"[src] serial open {self.port} @ {self.baud}")
                return
            except Exception as e:
                if first:
                    print(f"[src] waiting for {self.port} ({e}) - "
                          "is the receiver on and Bluetooth paired?")
                    first = False
                time.sleep(3)

    def write(self, data):
        with self.wlock:
            try:
                if self.ser:
                    self.ser.write(data)
            except Exception as e:
                print("[rtcm] serial write failed:", e)

    def read_loop(self, hub, state):
        buf = b""
        count = 0
        last_gga = ""
        last_print = time.monotonic()
        nonascii = 0
        while True:
            try:
                chunk = self.ser.read(256)
            except Exception as e:
                print(f"[src] serial read error ({e}) - Bluetooth dropout? reconnecting")
                try: self.ser.close()
                except Exception: pass
                self.ser = None
                buf = b""
                self._open()
                continue
            now = time.monotonic()
            if now - last_print > 4:
                if count:
                    print(f"[src] NMEA flowing: {count} sentences  |  {gga_summary(last_gga)}")
                elif nonascii:
                    print(f"[src] receiving DATA but no NMEA ({nonascii} bytes) - the receiver "
                          "is likely sending UBX/RTCM, not NMEA. Enable NMEA messages / Rover mode.")
                else:
                    print("[src] connected, but NO data from the receiver yet - is it in Rover "
                          "mode with NMEA output enabled, and out under open sky?")
                count = 0; nonascii = 0
                last_print = now
            if not chunk:
                continue
            if not any(10 == b or 36 == b for b in chunk):  # no LF and no '$'
                nonascii += len(chunk)
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                s = line.decode("ascii", "ignore").strip()
                if not s:
                    continue
                hub.broadcast(s + "\n")
                count += 1
                if s.startswith("$") and "GGA" in s[:7]:
                    state.set_gga(s)
                    last_gga = s


# --------------------------------------------------------------------------
# NTRIP client: pull RTCM from a caster, write to the receiver, push GGA up
# --------------------------------------------------------------------------
_FIX = {"0": "No Fix", "1": "GPS (standalone)", "2": "DGPS",
        "4": "RTK FIXED", "5": "RTK FLOAT", "6": "DeadReckon"}

def gga_summary(line):
    """Short human summary of a GGA sentence for the console heartbeat."""
    p = line.split(",")
    if len(p) > 8 and "GGA" in p[0]:
        return f"fix={_FIX.get(p[6], p[6] or '?')}  sats={p[7] or '?'}  hdop={p[8] or '?'}"
    return "waiting for GGA"


def parse_ntrip_url(u):
    """ntrip://user:pass@host:port/MOUNT  (scheme + creds optional)."""
    for pre in ("ntrip://", "http://", "https://"):
        if u.startswith(pre):
            u = u[len(pre):]
    user = pwd = None
    if "@" in u:
        creds, u = u.rsplit("@", 1)
        if ":" in creds:
            user, pwd = creds.split(":", 1)
        else:
            user = creds
    hostport, _, mount = u.partition("/")
    host, _, port = hostport.partition(":")
    return host, int(port) if port else 2101, mount, user, pwd


class NTRIPClient(threading.Thread):
    def __init__(self, url, writer, state, gga_seed=None):
        super().__init__(daemon=True)
        self.host, self.port, self.mount, self.user, self.pwd = parse_ntrip_url(url)
        self.writer = writer
        self.state = state
        self.gga_seed = gga_seed
        self.stop = False
        self.bytes = 0

    def _request(self):
        req = (f"GET /{self.mount} HTTP/1.1\r\n"
               f"Host: {self.host}:{self.port}\r\n"
               "Ntrip-Version: Ntrip/2.0\r\n"
               "User-Agent: NTRIP rtk_bridge/1.0\r\n")
        if self.user is not None:
            tok = base64.b64encode(f"{self.user}:{self.pwd or ''}".encode()).decode()
            req += f"Authorization: Basic {tok}\r\n"
        req += "Connection: close\r\n\r\n"
        return req.encode()

    def run(self):
        print(f"[ntrip] caster {self.host}:{self.port} mount /{self.mount}")
        while not self.stop:
            try:
                s = socket.create_connection((self.host, self.port), timeout=15)
                s.sendall(self._request())
                # read response header
                hdr = b""
                s.settimeout(15)
                while b"\r\n\r\n" not in hdr and b"\n\n" not in hdr:
                    b = s.recv(1)
                    if not b:
                        break
                    hdr += b
                    if len(hdr) > 4096:
                        break
                first = hdr.decode("latin-1", "ignore").split("\n", 1)[0].strip()
                if ("200" not in first) and ("ICY 200" not in first):
                    print(f"[ntrip] caster refused: {first!r} - check mountpoint/credentials")
                    time.sleep(8); continue
                print(f"[ntrip] streaming corrections from /{self.mount}")
                # initial GGA so VRS/network casters start sending data
                gga = self.state.get_gga() or self.gga_seed
                if gga:
                    try: s.sendall((gga + "\r\n").encode())
                    except Exception: pass
                last_gga = time.monotonic()
                last_log = time.monotonic()
                s.settimeout(1)
                while not self.stop:
                    now = time.monotonic()
                    if now - last_gga > 10:
                        g = self.state.get_gga() or self.gga_seed
                        if g:
                            try: s.sendall((g + "\r\n").encode())
                            except Exception: pass
                        last_gga = now
                    try:
                        data = s.recv(4096)
                    except socket.timeout:
                        continue
                    if not data:
                        print("[ntrip] stream ended, reconnecting")
                        break
                    self.writer(data)
                    self.bytes += len(data)
                    if now - last_log > 5:
                        print(f"[ntrip] corrections flowing: {self.bytes} bytes total")
                        last_log = now
                try: s.close()
                except Exception: pass
            except Exception as e:
                print("[ntrip] error:", e, "- retrying")
                time.sleep(8)


# --------------------------------------------------------------------------
# Other sources
# --------------------------------------------------------------------------
def run_tcp(spec, hub, state):
    _, host, port = spec.split(":")
    port = int(port)
    print(f"[src] connecting TCP {host}:{port}")
    s = socket.create_connection((host, port), timeout=10)
    buf = b""
    while True:
        chunk = s.recv(512)
        if not chunk:
            sys.exit("[src] TCP source closed")
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            t = line.decode("ascii", "ignore").strip()
            if t:
                hub.broadcast(t + "\n")
                if t.startswith("$") and "GGA" in t[:7]:
                    state.set_gga(t)


def find_bases(caster, lat, lon, top=15):
    """Fetch an NTRIP caster's sourcetable and list the nearest mountpoints."""
    import math
    host, _, port = caster.partition(":")
    port = int(port or 2101)
    print(f"[find] fetching sourcetable from {host}:{port} ...")
    s = socket.create_connection((host, port), timeout=20)
    s.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\n"
               "Ntrip-Version: Ntrip/2.0\r\nUser-Agent: NTRIP rtk_bridge/1.0\r\n"
               "Connection: close\r\n\r\n").encode())
    data = b""
    s.settimeout(20)
    while True:
        try:
            chunk = s.recv(16384)
        except socket.timeout:
            break
        if not chunk:
            break
        data += chunk
        if len(data) > 16_000_000:
            break
    def hav(la1, lo1, la2, lo2):
        p = math.pi / 180.0
        a = (math.sin((la2 - la1) * p / 2) ** 2 +
             math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2)
        return 2 * 6371.0 * math.asin(math.sqrt(a))   # km
    rows = []
    for line in data.decode("latin-1", "ignore").splitlines():
        if not line.startswith("STR;"):
            continue
        f = line.split(";")
        if len(f) < 11:
            continue
        try:
            blat, blon = float(f[9]), float(f[10])
        except ValueError:
            continue
        rows.append((hav(lat, lon, blat, blon), f[1], f[3], f[8]))
    if not rows:
        print("[find] no parseable mountpoints (caster may need a login to list).")
        return
    rows.sort(key=lambda r: r[0])
    print(f"[find] nearest mountpoints to {lat:.5f},{lon:.5f}  (km / mountpoint / format / country):")
    km_per_mi = 1.609344
    for d, mp, fmt, ctry in rows[:top]:
        flag = "  <-- great" if d <= 15 else ("  <-- usable" if d <= 35 else "")
        print(f"  {d:7.1f} km ({d/km_per_mi:5.1f} mi)  {mp:24.24} {fmt:14.14} {ctry}{flag}")
    print("\n[find] For a ZED-F9P rover, a base within ~15 km is ideal, up to ~35 km usable.")
    print("[find] Use one with:  --ntrip ntrip://USER:PASS@%s/MOUNTPOINT" % caster)


def nmea_checksum(body):
    c = 0
    for ch in body:
        c ^= ord(ch)
    return f"{c:02X}"


def run_test(hub, state):
    import math
    print("[src] TEST mode - synthetic RTK-fixed NMEA")
    lat0, lon0 = 39.49700, -105.30500
    t = 0.0
    while True:
        lat = lat0 + 0.0006 * math.sin(t)
        lon = lon0 + 0.0006 * math.cos(t) * 0.78
        la = int(abs(lat)); lm = (abs(lat) - la) * 60
        lo = int(abs(lon)); om = (abs(lon) - lo) * 60
        body = (f"GPGGA,120000.00,{la:02d}{lm:08.5f},N,"
                f"{lo:03d}{om:08.5f},W,4,24,0.6,2400.0,M,-20.0,M,,")
        line = f"${body}*{nmea_checksum(body)}"
        hub.broadcast(line + "\n")
        state.set_gga(line)
        t += 0.05
        time.sleep(0.2)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="NMEA <-> WebSocket + NTRIP bridge")
    ap.add_argument("--source", default="test",
                    help="serial:PORT[:BAUD] | tcp:HOST:PORT | test")
    ap.add_argument("--port", type=int, default=8765, help="WebSocket port")
    ap.add_argument("--ntrip", default=None,
                    help="NTRIP caster URL: ntrip://user:pass@host:port/MOUNTPOINT")
    ap.add_argument("--gga", default=None,
                    help="Optional seed GGA sentence to send to the caster before "
                         "the receiver produces one (helps VRS casters start)")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    ap.add_argument("--find-bases", metavar="LAT,LON", default=None,
                    help="list nearest NTRIP mountpoints to LAT,LON and exit "
                         "(e.g. --find-bases 39.503,-105.306)")
    ap.add_argument("--caster", default="rtk2go.com:2101",
                    help="caster host:port to scan for --find-bases (default rtk2go.com:2101)")
    args = ap.parse_args()

    if args.find_bases:
        try:
            la, lo = (float(x) for x in args.find_bases.split(","))
        except Exception:
            sys.exit("--find-bases expects LAT,LON e.g. 39.503,-105.306")
        find_bases(args.caster, la, lo)
        return

    if args.list:
        try:
            from serial.tools import list_ports
        except ImportError:
            sys.exit("pyserial not installed. Run:  pip install pyserial")
        ports = list(list_ports.comports())
        if not ports:
            print("No serial ports found.")
        for p in ports:
            print(f"  {p.device:12} {p.description}")
        return

    hub = WSHub(port=args.port)
    threading.Thread(target=hub.start, daemon=True).start()
    time.sleep(0.3)
    state = State()

    try:
        if args.source.startswith("serial:"):
            src = SerialSource(args.source)
            if args.ntrip:
                NTRIPClient(args.ntrip, src.write, state, gga_seed=args.gga).start()
            else:
                print("[bridge] no --ntrip given: relaying NMEA only (no corrections)")
            src.read_loop(hub, state)              # blocks
        elif args.source.startswith("tcp:"):
            if args.ntrip:
                print("[bridge] --ntrip needs a serial source to inject corrections; ignoring")
            run_tcp(args.source, hub, state)
        elif args.source == "test":
            run_test(hub, state)
        else:
            sys.exit(f"Unknown source: {args.source}")
    except KeyboardInterrupt:
        print("\n[bridge] stopped")


if __name__ == "__main__":
    main()
