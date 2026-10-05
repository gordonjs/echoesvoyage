#!/usr/bin/env python3
"""
Parcel RTK bridge  -  connects your RTK receiver to the Parcel RTK web app.

Run it with no arguments:      python rtk_bridge.py
It then:
  * talks to the receiver (Bluetooth or USB COM port - found automatically),
  * feeds RTK corrections from your NTRIP service into the receiver,
  * serves the app at http://localhost:8000/  and opens it,
  * keeps your project (calibration, saved points) in rtk_data/project.json,
  * lets a phone or tablet on the same Wi-Fi open the same live view.

Settings live in rtk_config.json (created on first run, edited from the app's
Settings tab). Command-line options still work and override the file:

  --source serial:COM8:115200 | serial:auto | tcp:HOST:PORT | test[:LAT,LON]
  --ntrip  ntrip://USER:PASS@HOST:PORT/MOUNT
  --save          store the command-line settings in rtk_config.json
  --port 8000     web port            --no-browser   don't open the app
  --no-lan        localhost only (no phone access)
  --list          list serial ports and exit
  --find-bases LAT,LON [--caster HOST:PORT]   nearest NTRIP bases
  --update        download the latest version from GitHub (keeps your data)
  --install       put a "Parcel RTK" launcher on your Desktop (Windows)

Only dependency: pyserial (pip install pyserial) for serial/Bluetooth ports.
"""
import argparse
import base64
import collections
import hashlib
import http.server
import io
import json
import math
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile

VERSION = "2.0.0"
APP_ID = "parcel-rtk"
HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
CONFIG_PATH = os.path.join(HERE, "rtk_config.json")
DATA_DIR = os.path.join(HERE, "rtk_data")
PROJECT_PATH = os.path.join(DATA_DIR, "project.json")
TILE_DIR = os.path.join(DATA_DIR, "tiles")
REPO = "gordonjs/echoesvoyage"
DEFAULT_BRANCH = "claude/rtk-surveyor-map-app-qb9n84"
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
USER_AGENT = "ParcelRTK/" + VERSION

DEFAULT_CONFIG = {
    "source": {"type": "serial", "port": "auto", "baud": 115200, "host": "", "tcpPort": 0},
    "ntrip": {"enabled": False, "host": "", "port": 2101, "mount": "", "user": "", "pass": "",
              "ggaInterval": 10},
    "web": {"port": 8000, "lan": True, "openBrowser": True, "appWindow": True},
    "update": {"branch": DEFAULT_BRANCH},
    "tiles": {},
    "logNmea": False,
}

# --------------------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------------------
LOG = collections.deque(maxlen=400)
_print_lock = threading.Lock()


def log(msg, tag="bridge"):
    line = "%s [%s] %s" % (time.strftime("%H:%M:%S"), tag, msg)
    LOG.append(line)
    with _print_lock:
        try:
            print(line, flush=True)
        except Exception:
            pass


def setup_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass


def deep_merge(base, over):
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def atomic_write(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class RateMeter:
    """bytes (or events) per second over a sliding window"""

    def __init__(self, window=5.0):
        self.window, self.ev, self.lock = window, collections.deque(), threading.Lock()

    def add(self, n=1):
        now = time.time()
        with self.lock:
            self.ev.append((now, n))
            while self.ev and now - self.ev[0][0] > self.window:
                self.ev.popleft()

    def rate(self):
        now = time.time()
        with self.lock:
            while self.ev and now - self.ev[0][0] > self.window:
                self.ev.popleft()
            return sum(n for _, n in self.ev) / self.window


# --------------------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------------------
def load_config(path=None):
    path = path or CONFIG_PATH                  # looked up at call time so tests can redirect it
    if not os.path.exists(path):
        return deep_merge(DEFAULT_CONFIG, {}), False
    try:
        with open(path, encoding="utf-8") as f:
            return deep_merge(DEFAULT_CONFIG, json.load(f)), True
    except Exception as e:
        bad = path + ".bad"
        try:
            shutil.copyfile(path, bad)
        except OSError:
            pass
        log("rtk_config.json could not be read (%s); using defaults. A copy was kept as %s" % (e, bad))
        return deep_merge(DEFAULT_CONFIG, {}), False


def save_config(cfg, path=None):
    atomic_write(path or CONFIG_PATH, json.dumps(cfg, indent=2))


def public_config(cfg):
    """config as sent to browsers: the NTRIP password never leaves the laptop"""
    c = json.loads(json.dumps(cfg))
    c["ntrip"]["passSet"] = bool(cfg["ntrip"].get("pass"))
    c["ntrip"]["pass"] = ""
    return c


def parse_ntrip_url(u):
    """ntrip://user:pass@host:port/MOUNT -> dict (password may contain : @ ^ & /)"""
    for pre in ("ntrip://", "http://", "https://"):
        if u.startswith(pre):
            u = u[len(pre):]
    user = pw = ""
    if "@" in u:
        creds, u = u.rsplit("@", 1)
        user, _, pw = creds.partition(":")
    hostport, _, mount = u.partition("/")
    host, _, port = hostport.partition(":")
    return {"host": host, "port": int(port) if port else 2101, "mount": mount, "user": user, "pass": pw}


def parse_source(spec):
    spec = spec.strip()
    if spec.startswith("serial:"):
        parts = spec.split(":")
        # serial:COM8:115200  |  serial:/dev/ttyUSB0:115200  |  serial:auto
        port = parts[1] if len(parts) > 1 and parts[1] else "auto"
        baud = int(parts[2]) if len(parts) > 2 and parts[2] else 115200
        return {"type": "serial", "port": port, "baud": baud}
    if spec.startswith("tcp:"):
        _, host, port = spec.split(":")
        return {"type": "tcp", "host": host, "tcpPort": int(port)}
    if spec == "auto":
        return {"type": "serial", "port": "auto", "baud": 115200}
    if spec.startswith("test"):
        out = {"type": "test"}
        if ":" in spec:
            lat, lon = spec.split(":", 1)[1].split(",")
            out["center"] = [float(lat), float(lon)]
        return out
    raise ValueError("unknown --source: " + spec)


# --------------------------------------------------------------------------------------
# NMEA
# --------------------------------------------------------------------------------------
FIX_NAMES = {0: "No fix", 1: "GPS (standalone)", 2: "DGPS", 4: "RTK FIXED", 5: "RTK FLOAT", 6: "Dead reckoning"}


def nmea_ok(line):
    if not line.startswith("$"):
        return False
    star = line.rfind("*")
    if star < 0 or star + 3 > len(line):
        return False
    c = 0
    for ch in line[1:star]:
        c ^= ord(ch)
    try:
        return c == int(line[star + 1:star + 3], 16)
    except ValueError:
        return False


def nmea_with_checksum(body):
    c = 0
    for ch in body:
        c ^= ord(ch)
    return "$%s*%02X" % (body, c)


def nmea_coord(v, hemi):
    if not v or "." not in v:
        return None
    dot = v.index(".")
    try:
        d = int(v[:dot - 2]) + float(v[dot - 2:]) / 60
    except ValueError:
        return None
    return -d if hemi in ("S", "W") else d


def gga_info(line):
    f = line.split("*")[0].split(",")
    if len(f) < 10 or not f[0].endswith("GGA"):
        return None
    try:
        q = int(f[6] or 0)
    except ValueError:
        q = 0
    return {"q": q, "sats": f[7], "hdop": f[8], "lat": nmea_coord(f[2], f[3]), "lon": nmea_coord(f[4], f[5]),
            "age": f[13] if len(f) > 13 else ""}


# --------------------------------------------------------------------------------------
# RTCM 3 (to verify and describe the correction stream)
# --------------------------------------------------------------------------------------
def _crc24q_table():
    t = []
    for i in range(256):
        c = i << 16
        for _ in range(8):
            c <<= 1
            if c & 0x1000000:
                c ^= 0x1864CFB
        t.append(c & 0xFFFFFF)
    return t


CRC24Q = _crc24q_table()


def crc24q(data):
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFFFF) ^ CRC24Q[(crc >> 16) ^ b]
    return crc


def getbits(buf, pos, n):
    v = 0
    for i in range(pos, pos + n):
        v = (v << 1) | ((buf[i >> 3] >> (7 - (i & 7))) & 1)
    return v


def getsbits(buf, pos, n):
    v = getbits(buf, pos, n)
    return v - (1 << n) if v & (1 << (n - 1)) else v


WGS_A, WGS_E2 = 6378137.0, 6.69437999014e-3


def ecef_to_geo(x, y, z):
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - WGS_E2))
    h = 0.0
    for _ in range(8):
        n = WGS_A / math.sqrt(1 - WGS_E2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - WGS_E2 * n / (n + h)))
    return math.degrees(lat), math.degrees(lon), h


def geo_to_ecef(lat, lon, h=0.0):
    la, lo = math.radians(lat), math.radians(lon)
    n = WGS_A / math.sqrt(1 - WGS_E2 * math.sin(la) ** 2)
    return ((n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo),
            (n * (1 - WGS_E2) + h) * math.sin(la))


def decode_1005(p):
    """RTCM 1005/1006 station antenna reference point -> (station_id, lat, lon, h)"""
    if len(p) < 19:
        return None
    stn = getbits(p, 12, 12)
    x, y, z = getsbits(p, 34, 38) * 1e-4, getsbits(p, 74, 38) * 1e-4, getsbits(p, 114, 38) * 1e-4
    lat, lon, h = ecef_to_geo(x, y, z)
    return {"station": stn, "lat": lat, "lon": lon, "h": h, "ecef": (x, y, z)}


class RtcmScanner:
    """finds CRC-valid RTCM 3 frames in a byte stream"""

    def __init__(self):
        self.buf = bytearray()
        self.counts = collections.Counter()
        self.bad = 0
        self.frames = 0
        self.base = None
        self.first_byte = None

    def feed(self, data):
        if self.first_byte is None and data:
            self.first_byte = data[0]
        self.buf += data
        while True:
            i = self.buf.find(b"\xd3")
            if i < 0:
                self.buf.clear()
                return
            if i:
                del self.buf[:i]
            if len(self.buf) < 3:
                return
            ln = ((self.buf[1] & 0x03) << 8) | self.buf[2]
            total = ln + 6
            if (self.buf[1] & 0xFC) or len(self.buf) < total:
                # a fake preamble (reserved bits set, or a frame we can't complete yet): if a
                # complete CRC-valid frame starts later in the buffer, skip to it now instead of
                # waiting up to 1 KB for bytes that will never validate.
                j = self._next_valid(1)
                if j is not None:
                    del self.buf[:j]
                    continue
                if (self.buf[1] & 0xFC) or len(self.buf) > 4096:
                    del self.buf[:1]
                    continue
                return
            frame = bytes(self.buf[:total])
            if ln >= 2 and crc24q(frame[:-3]) == int.from_bytes(frame[-3:], "big"):
                payload = frame[3:-3]
                mt = (payload[0] << 4) | (payload[1] >> 4)
                self.counts[mt] += 1
                self.frames += 1
                if mt in (1005, 1006):
                    self.base = decode_1005(payload) or self.base
                del self.buf[:total]
            else:
                self.bad += 1
                del self.buf[:1]

    def _next_valid(self, start):
        b = self.buf
        j = b.find(b"\xd3", start)
        while j >= 0 and j + 3 <= len(b):
            if not (b[j + 1] & 0xFC):
                total = (((b[j + 1] & 0x03) << 8) | b[j + 2]) + 6
                if j + total <= len(b) and crc24q(bytes(b[j:j + total - 3])) == int.from_bytes(b[j + total - 3:j + total], "big"):
                    return j
            j = b.find(b"\xd3", j + 1)
        return None

    def describe(self):
        if self.first_byte == 0x73 and self.frames == 0:
            return "SPARTN"
        if self.frames:
            return "RTCM3"
        return "unknown" if self.first_byte is not None else None


def encode_rtcm(payload):
    """wrap a payload into an RTCM3 frame (used by the test source and tests)"""
    hdr = bytes([0xD3, (len(payload) >> 8) & 0x03, len(payload) & 0xFF])
    body = hdr + payload
    return body + crc24q(body).to_bytes(3, "big")


def make_1005(station, x, y, z):
    bits = []

    def put(v, n):
        v &= (1 << n) - 1
        bits.extend((v >> (n - 1 - i)) & 1 for i in range(n))
    put(1005, 12); put(station, 12); put(0, 6); put(1, 1); put(1, 1); put(1, 1); put(0, 1)
    put(int(round(x * 1e4)), 38); put(0, 1); put(0, 1)
    put(int(round(y * 1e4)), 38); put(0, 2)
    put(int(round(z * 1e4)), 38)
    while len(bits) % 8:
        bits.append(0)
    return bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


# --------------------------------------------------------------------------------------
# serial ports
# --------------------------------------------------------------------------------------
def classify_port(device, description, hwid):
    d, h = (description or "").lower(), (hwid or "").upper()
    if "BTHENUM" in h or "bluetooth" in d:
        # Windows makes two COM ports per Bluetooth SPP device. The *outgoing* one (the
        # one we open) carries the remote device's address in its hardware id; the
        # incoming one has all zeros.
        m = re.search(r"&([0-9A-F]{12})_", h)
        if m and m.group(1) == "0" * 12:
            return "bt-in"
        return "bt-out"
    if any(k in d for k in ("ch340", "ch910", "usb serial", "usb-serial", "cp210", "ftdi", "u-blox", "usb")) or "USB" in h:
        return "usb"
    return "other"


def list_ports():
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return None
    out = []
    for p in lp.comports():
        kind = classify_port(p.device, p.description, p.hwid)
        out.append({"device": p.device, "description": p.description, "kind": kind})
    rank = {"bt-out": 0, "usb": 1, "other": 2, "bt-in": 3}
    out.sort(key=lambda p: (rank[p["kind"]], p["device"]))
    return out


def open_error_kind(e):
    s = str(e).lower()
    if "access is denied" in s or "permission" in s or "busy" in s or "resource busy" in s:
        return "busy", "is being used by another program (an old bridge window?)"
    if "semaphore timeout" in s or "timed out" in s:
        return "noresponse", "did not respond — is the receiver on, paired and in range?"
    if "filenotfound" in s or "cannot find" in s or "no such file" in s or "could not open port" in s:
        return "missing", "was not found"
    return "error", str(e)


def probe_port(device, baud, seconds, stop=None):
    """open a port briefly and count valid NMEA sentences"""
    import serial
    try:
        ser = serial.Serial(device, baud, timeout=0.25)
    except Exception as e:
        return 0, open_error_kind(e)[0]
    good, buf, t0 = 0, b"", time.time()
    try:
        while time.time() - t0 < seconds and good < 3 and not (stop and stop.is_set()):
            buf += ser.read(512)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if nmea_ok(line.decode("ascii", "ignore").strip()):
                    good += 1
    finally:
        try:
            ser.close()
        except Exception:
            pass
    return good, "ok" if good else "silent"


# --------------------------------------------------------------------------------------
# receiver sources
# --------------------------------------------------------------------------------------
class SourceBase(threading.Thread):
    def __init__(self, bridge, name):
        super().__init__(daemon=True, name=name)
        self.bridge = bridge
        self.stop_ev = threading.Event()
        self.state, self.detail = "starting", ""
        self.nmea_rate, self.bad = RateMeter(), 0
        self.last_rx = 0.0
        self.tx_bytes = 0
        self.label = name

    def stop(self):
        self.stop_ev.set()

    def deliver(self, chunk_lines):
        if chunk_lines:
            self.last_rx = time.time()
            self.nmea_rate.add(len(chunk_lines))
            self.bridge.on_nmea(chunk_lines)

    def status(self):
        silent = self.last_rx and time.time() - self.last_rx > 5
        return {"type": self.label, "state": "silent" if (self.state == "open" and silent) else self.state,
                "detail": self.detail, "nmeaPerSec": round(self.nmea_rate.rate(), 1), "badChecksums": self.bad,
                "txBytes": self.tx_bytes}

    def split(self, buf):
        lines = []
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            s = raw.decode("ascii", "ignore").strip()
            if not s:
                continue
            if s.startswith("$"):
                if nmea_ok(s):
                    lines.append(s)
                else:
                    self.bad += 1
        if len(buf) > 4096:
            buf = buf[-1024:]
        return lines, buf


class SerialSource(SourceBase):
    def __init__(self, bridge, port, baud):
        super().__init__(bridge, "serial")
        self.port, self.baud = port, baud
        self.ser = None
        self.wlock = threading.Lock()

    def status(self):
        s = super().status()
        s.update(port=self.port, baud=self.baud)
        return s

    def write(self, data):
        with self.wlock:
            if self.ser is None:
                return False
            try:
                self.ser.write(data)
                self.tx_bytes += len(data)
                return True
            except Exception as e:
                log("write to receiver failed: %s" % e, "rx")
                return False

    def autodetect(self):
        ports = list_ports() or []
        cands = [p for p in ports if p["kind"] != "bt-in"]
        if not cands:
            self.state, self.detail = "noport", "no COM ports found — pair the receiver (Bluetooth: Classic/SPP) or plug in USB"
            return None
        for p in cands:
            if self.stop_ev.is_set():
                return None
            self.state, self.detail = "probing", "looking for the receiver on %s (%s)…" % (p["device"], p["description"])
            log(self.detail, "rx")
            good, why = probe_port(p["device"], self.baud, 12 if p["kind"] == "bt-out" else 4, self.stop_ev)
            if good:
                log("receiver found on %s" % p["device"], "rx")
                return p["device"]
        self.state, self.detail = "notfound", "no port is sending GPS data — is the receiver on and in range?"
        return None

    def run(self):
        import serial
        configured = self.port
        misses = 0
        while not self.stop_ev.is_set():
            port = self.port
            if port in ("", "auto") or misses >= 2:
                found = self.autodetect()
                if not found:
                    self.stop_ev.wait(5)
                    continue
                port = found
                if configured not in ("", "auto") and found != configured:
                    log("%s is gone; using %s instead and remembering it" % (configured, found), "rx")
                    self.bridge.remember_port(found)
                self.port = port
                misses = 0
            self.state, self.detail = "opening", "opening %s…" % port
            try:
                ser = serial.Serial(port, self.baud, timeout=0.2)
            except Exception as e:
                kind, why = open_error_kind(e)
                self.state, self.detail = kind, "%s %s" % (port, why)
                log(self.detail, "rx")
                misses = misses + 1 if kind in ("missing", "noresponse") else 0
                self.stop_ev.wait(3)
                continue
            with self.wlock:
                self.ser = ser
            self.state, self.detail = "open", port
            log("connected to receiver on %s" % port, "rx")
            misses, buf = 0, b""
            try:
                while not self.stop_ev.is_set():
                    chunk = ser.read(4096)
                    if chunk:
                        lines, buf = self.split(buf + chunk)
                        self.deliver(lines)
            except Exception as e:
                log("receiver link dropped (%s) — reconnecting" % e, "rx")
                self.state, self.detail = "dropped", "link dropped — reconnecting"
            finally:
                with self.wlock:
                    self.ser = None
                try:
                    ser.close()
                except Exception:
                    pass
            self.stop_ev.wait(1)
        self.state = "stopped"


class TcpSource(SourceBase):
    def __init__(self, bridge, host, port):
        super().__init__(bridge, "tcp")
        self.host, self.port, self.sock = host, port, None
        self.wlock = threading.Lock()

    def write(self, data):
        with self.wlock:
            if not self.sock:
                return False
            try:
                self.sock.sendall(data)
                self.tx_bytes += len(data)
                return True
            except OSError:
                return False

    def run(self):
        while not self.stop_ev.is_set():
            self.state, self.detail = "opening", "%s:%s" % (self.host, self.port)
            try:
                s = socket.create_connection((self.host, self.port), timeout=10)
                s.settimeout(1.0)
            except OSError as e:
                self.state, self.detail = "error", "%s:%s %s" % (self.host, self.port, e)
                self.stop_ev.wait(5)
                continue
            with self.wlock:
                self.sock = s
            self.state, buf = "open", b""
            try:
                while not self.stop_ev.is_set():
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    lines, buf = self.split(buf + chunk)
                    self.deliver(lines)
            except OSError:
                pass
            finally:
                with self.wlock:
                    self.sock = None
                s.close()
            self.state = "dropped"
            self.stop_ev.wait(2)


class TestSource(SourceBase):
    """synthetic RTK-fixed receiver walking a slow figure-eight; accepts RTCM writes"""

    def __init__(self, bridge, center=None):
        super().__init__(bridge, "test")
        self.center = center or [39.4970, -105.3050]          # near the property (Conifer, CO)

    def write(self, data):
        self.tx_bytes += len(data)
        return True

    def run(self):
        self.state, self.detail = "open", "synthetic receiver"
        t = 0.0
        lat0, lon0 = self.center
        while not self.stop_ev.is_set():
            dy, dx = 120 * math.sin(t), 160 * math.sin(2 * t) / 2
            lat = lat0 + dy / 111026.0
            lon = lon0 + dx / 85952.0
            hh = time.strftime("%H%M%S", time.gmtime()) + ".%02d" % int((time.time() % 1) * 100)

            def dm(v, w):
                a = abs(v)
                d = int(a)
                return "%0*d%011.8f" % (w, d, (a - d) * 60)
            gga = nmea_with_checksum("GNGGA,%s,%s,%s,%s,%s,4,18,0.55,%.3f,M,-15.400,M,1.0,0000" % (
                hh, dm(lat, 2), "N" if lat >= 0 else "S", dm(lon, 3), "W" if lon < 0 else "E", 2655.1))
            gst = nmea_with_checksum("GNGST,%s,0.010,0.009,0.007,45.0,0.009,0.007,0.015" % hh)
            cog = (math.degrees(math.atan2(math.cos(2 * t), math.cos(t))) + 360) % 360
            rmc = nmea_with_checksum("GNRMC,%s,A,%s,%s,%s,%s,0.8,%.1f,%s,,,D" % (
                hh, dm(lat, 2), "N", dm(lon, 3), "W", cog, time.strftime("%d%m%y", time.gmtime())))
            self.deliver([gga, gst, rmc])
            t += 0.01
            self.stop_ev.wait(0.5)


# --------------------------------------------------------------------------------------
# NTRIP client
# --------------------------------------------------------------------------------------
class CasterRefused(Exception):
    pass


class Dechunker:
    """HTTP/1.1 chunked transfer decoding (NTRIP v2 casters may use it)"""

    def __init__(self):
        self.buf, self.state, self.need = b"", "SIZE", 0

    def feed(self, data):
        self.buf += data
        out = bytearray()
        while self.buf:
            if self.state == "SIZE":
                i = self.buf.find(b"\r\n")
                if i < 0:
                    if len(self.buf) > 64:
                        raise ValueError("bad chunk header")
                    break
                line = self.buf[:i].split(b";", 1)[0].strip()
                self.buf = self.buf[i + 2:]
                if not line:
                    continue
                self.need = int(line, 16)
                self.state = "DATA" if self.need else "END"
            elif self.state == "DATA":
                take = self.buf[:self.need]
                out += take
                self.buf = self.buf[len(take):]
                self.need -= len(take)
                if not self.need:
                    self.state = "CRLF"
            elif self.state == "CRLF":
                if len(self.buf) < 2:
                    break
                self.buf, self.state = self.buf[2:], "SIZE"
            else:
                self.buf = b""
        return bytes(out)


class NtripClient(threading.Thread):
    def __init__(self, bridge, cfg):
        super().__init__(daemon=True, name="ntrip")
        self.bridge, self.cfg = bridge, dict(cfg)
        self.stop_ev = threading.Event()
        self.state, self.detail = "starting", ""
        self.rate = RateMeter()
        self.total = 0
        self.scanner = RtcmScanner()
        self.since = None
        self.sock = None

    def stop(self):
        self.stop_ev.set()
        s = self.sock
        if s:
            try:
                s.close()
            except OSError:
                pass

    def status(self):
        sc = self.scanner
        base = None
        if sc.base:
            base = {"station": sc.base["station"], "lat": sc.base["lat"], "lon": sc.base["lon"]}
            rover = self.bridge.last_fix
            if rover and rover.get("lat") is not None:
                rx, ry, rz = geo_to_ecef(rover["lat"], rover["lon"], sc.base["h"])
                bx, by, bz = sc.base["ecef"]
                base["distKm"] = round(math.sqrt((rx - bx) ** 2 + (ry - by) ** 2 + (rz - bz) ** 2) / 1000, 2)
        types = sorted(sc.counts)
        return {"state": self.state, "detail": self.detail, "host": self.cfg.get("host"), "mount": self.cfg.get("mount"),
                "bytesPerSec": round(self.rate.rate()), "total": self.total, "format": sc.describe(),
                "types": types[:24], "badFrames": sc.bad, "base": base,
                "upSec": int(time.time() - self.since) if self.since else 0}

    def request(self, gga):
        c = self.cfg
        lines = ["GET /%s HTTP/1.1" % c["mount"], "Host: %s:%s" % (c["host"], c["port"]),
                 "Ntrip-Version: Ntrip/2.0", "User-Agent: NTRIP " + USER_AGENT, "Connection: close"]
        if c.get("user"):
            tok = base64.b64encode(("%s:%s" % (c["user"], c.get("pass", ""))).encode()).decode()
            lines.append("Authorization: Basic " + tok)
        if gga:
            lines.append("Ntrip-GGA: " + gga)
        return ("\r\n".join(lines) + "\r\n\r\n").encode()

    def run(self):
        backoff = 2
        while not self.stop_ev.is_set():
            try:
                self.session()
                backoff = 2
            except CasterRefused as e:
                self.state, self.detail = "refused", str(e)
                log("caster refused: %s" % e, "ntrip")
                self.stop_ev.wait(30)
            except Exception as e:
                if self.stop_ev.is_set():
                    break
                self.state, self.detail = "error", str(e) or e.__class__.__name__
                log("%s — retrying in %ds" % (self.detail, backoff), "ntrip")
                self.stop_ev.wait(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                self.since = None
        self.state = "stopped"

    def session(self):
        c = self.cfg
        self.state, self.detail = "connecting", "%s:%s/%s" % (c["host"], c["port"], c["mount"])
        gga = self.bridge.last_gga_line
        s = socket.create_connection((c["host"], int(c["port"])), timeout=15)
        self.sock = s
        try:
            s.sendall(self.request(gga))
            s.settimeout(15)
            head = b""
            while b"\r\n" not in head:
                part = s.recv(1024)
                if not part:
                    raise ConnectionError("caster closed the connection before answering")
                head += part
            first, rest = head.split(b"\r\n", 1)
            status = first.decode("latin-1").strip()
            headers = {}
            if status.startswith("ICY"):
                if rest.startswith(b"\r\n"):
                    rest = rest[2:]
            else:
                while b"\r\n\r\n" not in (b"\r\n" + rest):
                    part = s.recv(4096)
                    if not part:
                        break
                    rest += part
                hdr, _, rest = (b"\r\n" + rest).partition(b"\r\n\r\n")
                for h in hdr.decode("latin-1").split("\r\n"):
                    k, _, v = h.partition(":")
                    if k:
                        headers[k.strip().lower()] = v.strip()
            if status.startswith("SOURCETABLE"):
                raise CasterRefused("mountpoint '%s' does not exist on this caster" % c["mount"])
            if " 401" in status or " 403" in status:
                raise CasterRefused("%s — check the username, password, and that your plan includes '%s'" % (status, c["mount"]))
            if not (status.startswith("ICY 200") or re.match(r"HTTP/1\.[01] 200", status)):
                raise ConnectionError("caster answered: " + status[:80])
            dech = Dechunker() if "chunked" in headers.get("transfer-encoding", "").lower() else None
            self.state, self.detail, self.since = "streaming", "%s/%s" % (c["host"], c["mount"]), time.time()
            log("streaming corrections from %s/%s%s" % (c["host"], c["mount"], " (chunked)" if dech else ""), "ntrip")
            s.settimeout(1.0)
            last_gga_sent = time.time() if gga else 0
            if gga:
                s.sendall((gga + "\r\n").encode())
            last_data = time.time()
            warned = False
            pending = rest
            while not self.stop_ev.is_set():
                if pending:
                    data, pending = pending, b""
                else:
                    try:
                        data = s.recv(8192)
                    except socket.timeout:
                        data = None
                    if data == b"":
                        raise ConnectionError("caster closed the connection")
                now = time.time()
                if data:
                    payload = dech.feed(data) if dech else data
                    if payload:
                        last_data = now
                        self.total += len(payload)
                        self.rate.add(len(payload))
                        self.scanner.feed(payload)
                        self.bridge.write_to_receiver(payload)
                        if not warned and self.total > 3000 and self.scanner.describe() == "SPARTN":
                            warned = True
                            log("this mountpoint sends SPARTN. Injected over Bluetooth the SparkFun receiver ignores it — "
                                "use an RTCM mountpoint (for PointPerfect: NEAR-RTCM)", "ntrip")
                if now - last_data > 30:
                    raise ConnectionError("no data from the caster for 30 s")
                gga = self.bridge.last_gga_line
                if gga and now - last_gga_sent >= max(2, int(c.get("ggaInterval") or 10)):
                    s.sendall((gga + "\r\n").encode())
                    last_gga_sent = now
        finally:
            self.sock = None
            try:
                s.close()
            except OSError:
                pass


def fetch_sourcetable(host, port, timeout=20):
    s = socket.create_connection((host, port), timeout=timeout)
    s.sendall(("GET / HTTP/1.1\r\nHost: %s:%s\r\nNtrip-Version: Ntrip/2.0\r\nUser-Agent: NTRIP %s\r\n"
               "Connection: close\r\n\r\n" % (host, port, USER_AGENT)).encode())
    data = b""
    s.settimeout(timeout)
    try:
        while len(data) < 16000000:
            part = s.recv(65536)
            if not part:
                break
            data += part
            if b"ENDSOURCETABLE" in data[-64:]:
                break
    except socket.timeout:
        pass
    finally:
        s.close()
    return data.decode("latin-1", "ignore")


def find_bases(caster, lat, lon, top=25, text=None):
    host, _, port = caster.partition(":")
    text = text if text is not None else fetch_sourcetable(host, int(port or 2101))
    rows = []
    for line in text.splitlines():
        if not line.startswith("STR;"):
            continue
        f = line.split(";")
        if len(f) < 12:
            continue
        try:
            blat, blon = float(f[9]), float(f[10])
        except ValueError:
            continue
        p1, p2 = math.radians(lat), math.radians(blat)
        a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(blon - lon) / 2) ** 2
        km = 2 * 6371.0 * math.asin(math.sqrt(a))
        rows.append({"mount": f[1], "km": round(km, 1), "format": f[3], "country": f[8], "lat": blat, "lon": blon,
                     "needsGga": f[11] == "1"})
    rows.sort(key=lambda r: r["km"])
    return rows[:top]


# --------------------------------------------------------------------------------------
# project store (same op language as web/js/state.js; see tests/ops_vectors.json)
# --------------------------------------------------------------------------------------
TOP_KEYS = ("rough", "control", "settings", "savedPoints")
BAD_KEYS = ("__proto__", "constructor", "prototype")
DEFAULT_SETTINGS = {"hold": True, "fitScale": False, "units": "ft", "base": "sat", "edgeAlertFt": 0, "roughSigmaFt": 10}


class OpError(ValueError):
    pass


def empty_state():
    return {"schema": 2, "rev": 0, "rough": None, "control": {}, "settings": dict(DEFAULT_SETTINGS), "savedPoints": []}


def state_is_empty(s):
    return not s.get("rough") and not s.get("control") and not s.get("savedPoints")


def apply_op(state, op):
    s = json.loads(json.dumps(state))
    if not isinstance(op, dict):
        raise OpError("not an object")
    path = op.get("path")
    if not isinstance(path, list) or not path:
        raise OpError("path")
    if path[0] not in TOP_KEYS:
        raise OpError("path must start with one of " + ",".join(TOP_KEYS))
    for k in path:
        if not isinstance(k, str) or k in BAD_KEYS:
            raise OpError("key")
    kind = op.get("op")
    parent = s
    for k in path[:-1]:
        node = parent.get(k)
        if isinstance(node, list):
            raise OpError("array in path")
        if not isinstance(node, dict):
            if kind == "set":
                parent[k] = {}
            else:
                raise OpError("missing " + k)
        parent = parent[k]
    key = path[-1]
    if kind == "set":
        parent[key] = json.loads(json.dumps(op.get("value")))
    elif kind == "del":
        if key not in parent:
            raise OpError("missing " + key)
        del parent[key]
    elif kind in ("push", "remove", "patch"):
        arr = parent.get(key)
        if not isinstance(arr, list):
            raise OpError("not an array")
        if kind == "push":
            v = op.get("value")
            if not isinstance(v, dict) or v.get("id") is None:
                raise OpError("value.id")
            if any(isinstance(x, dict) and x.get("id") == v["id"] for x in arr):
                raise OpError("duplicate id")
            arr.append(json.loads(json.dumps(v)))
        else:
            if op.get("id") is None:
                raise OpError("id")
            idx = next((i for i, x in enumerate(arr) if isinstance(x, dict) and x.get("id") == op.get("id")), -1)
            if idx < 0:
                raise OpError("no element %s" % op.get("id"))
            if kind == "remove":
                arr.pop(idx)
            else:
                v = op.get("value")
                if not isinstance(v, dict) or v.get("id") is not None:
                    raise OpError("patch value")
                arr[idx].update(json.loads(json.dumps(v)))
    else:
        raise OpError("unknown op %s" % kind)
    s["rev"] = int(state.get("rev") or 0) + 1
    return s


class ProjectStore:
    def __init__(self, path=None):
        self.path, self.lock = path or PROJECT_PATH, threading.Lock()
        self.state = self._load()

    def _load(self):
        for p in (self.path, self.path + ".bak"):
            try:
                with open(p, encoding="utf-8") as f:
                    st = json.load(f)
                if isinstance(st, dict) and st.get("schema") == 2:
                    st["settings"] = deep_merge(DEFAULT_SETTINGS, st.get("settings") or {})
                    if p.endswith(".bak"):
                        log("project.json was unreadable — restored the previous copy", "data")
                    return st
            except FileNotFoundError:
                continue
            except Exception as e:
                log("could not read %s: %s" % (os.path.basename(p), e), "data")
        return empty_state()

    def _save(self):
        if os.path.exists(self.path):
            try:
                shutil.copyfile(self.path, self.path + ".bak")
            except OSError:
                pass
        atomic_write(self.path, json.dumps(self.state, indent=1))

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def apply(self, op):
        with self.lock:
            self.state = apply_op(self.state, op)
            self._save()
            return self.state["rev"]

    def replace(self, new_state, only_if_empty=True):
        with self.lock:
            if only_if_empty and not state_is_empty(self.state):
                raise OpError("the bridge already has a project")
            if not isinstance(new_state, dict):
                raise OpError("state must be an object")
            st = empty_state()
            for k in TOP_KEYS:
                if k in new_state:
                    st[k] = new_state[k]
            if not isinstance(st["control"], dict) or not isinstance(st["savedPoints"], list):
                raise OpError("bad state shape")
            st["settings"] = deep_merge(DEFAULT_SETTINGS, st.get("settings") or {})
            st["rev"] = int(self.state.get("rev") or 0) + 1
            self.state = st
            self._save()
            return st["rev"]


# --------------------------------------------------------------------------------------
# map tile cache (so the satellite view keeps working without internet once seen)
# --------------------------------------------------------------------------------------
TILE_LAYERS = {
    "sat": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "topo": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
    "street": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
}


class TileCache:
    def __init__(self, root=None, overrides=None):
        self.root = root or TILE_DIR
        self.layers = dict(TILE_LAYERS)
        self.layers.update(overrides or {})
        self.neg = {}
        self.lock = threading.Lock()

    def get(self, layer, z, x, y):
        if layer not in self.layers or not (0 <= z <= 22) or not (0 <= x < 2 ** z) or not (0 <= y < 2 ** z):
            return None
        path = os.path.join(self.root, layer, str(z), str(x), str(y))
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            pass
        key = (layer, z, x, y)
        with self.lock:
            if self.neg.get(key, 0) > time.time():
                return None
        url = self.layers[layer].format(z=z, x=x, y=y)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT + " (personal property map)"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = r.read()
            if not data or not (data[:2] == b"\xff\xd8" or data[:4] == b"\x89PNG"):
                raise ValueError("not an image")
        except Exception:
            with self.lock:
                self.neg[key] = time.time() + 60
            return None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part%d" % threading.get_ident()
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except OSError:
            pass
        return data


# --------------------------------------------------------------------------------------
# WebSocket framing (RFC 6455) — minimal, server side
# --------------------------------------------------------------------------------------
def ws_frame(opcode, payload):
    n = len(payload)
    if n < 126:
        hdr = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 65536:
        hdr = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        hdr = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return hdr + payload


def _recv_exact(read, n):
    buf = b""
    while len(buf) < n:
        part = read(n - len(buf))
        if not part:
            raise ConnectionError("closed")
        buf += part
    return buf


def ws_read_message(read, max_size=8 * 1024 * 1024):
    """read(n) -> bytes. Returns (opcode, payload) of a complete message; control frames immediately."""
    chunks, op0 = [], None
    while True:
        b1, b2 = _recv_exact(read, 2)
        fin, op, masked, ln = b1 & 0x80, b1 & 0x0F, b2 & 0x80, b2 & 0x7F
        if ln == 126:
            ln = struct.unpack("!H", _recv_exact(read, 2))[0]
        elif ln == 127:
            ln = struct.unpack("!Q", _recv_exact(read, 8))[0]
        if ln > max_size:
            raise ConnectionError("message too large")
        mask = _recv_exact(read, 4) if masked else None
        data = bytearray(_recv_exact(read, ln)) if ln else bytearray()
        if mask:
            for i in range(len(data)):
                data[i] ^= mask[i & 3]
        if op >= 8:
            return op, bytes(data)
        if op != 0:
            op0 = op
        chunks.append(bytes(data))
        if sum(map(len, chunks)) > max_size:
            raise ConnectionError("message too large")
        if fin:
            return op0, b"".join(chunks)


class Client:
    def __init__(self, sock, addr):
        self.sock, self.addr = sock, addr
        self.local = addr[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
        self.q = queue.Queue(maxsize=3000)
        self.alive = True
        threading.Thread(target=self._sender, daemon=True, name="ws-send").start()

    def _sender(self):
        while self.alive:
            try:
                frame = self.q.get(timeout=1)
            except queue.Empty:
                continue
            if frame is None:
                break
            try:
                self.sock.sendall(frame)
            except OSError:
                break
        self.close()

    def send(self, obj):
        if not self.alive:
            return
        try:
            self.q.put_nowait(ws_frame(1, json.dumps(obj, separators=(",", ":")).encode()))
        except queue.Full:
            self.close()                            # a stalled phone never slows the receiver down

    def send_raw(self, frame):
        try:
            self.q.put_nowait(frame)
        except queue.Full:
            self.close()

    def close(self):
        if self.alive:
            self.alive = False
            try:
                self.q.put_nowait(None)
            except queue.Full:
                pass
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


# --------------------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------------------
class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"      # on Windows SO_REUSEADDR lets two copies share a port

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        # Browsers drop connections all the time (the map cancels tiles it no longer needs):
        # that is normal, not worth a traceback in the console.
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionError, TimeoutError, socket.timeout)):
            return
        log("web request from %s failed: %r" % (client_address[0], e), "web")


def make_handler(bridge):
    class Handler(http.server.SimpleHTTPRequestHandler):
        server_version = "ParcelRTK/" + VERSION
        protocol_version = "HTTP/1.1"             # browsers reject a WebSocket 101 sent as HTTP/1.0
        extensions_map = dict(http.server.SimpleHTTPRequestHandler.extensions_map, **{
            ".js": "text/javascript", ".css": "text/css", ".html": "text/html; charset=utf-8",
            ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".webmanifest": "application/manifest+json"})

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=WEB_DIR, **kw)

        def log_message(self, *a):
            pass

        def end_headers(self):
            if not getattr(self, "_custom_cache", False):
                self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Parcel-RTK", VERSION)
            super().end_headers()

        def list_directory(self, path):
            self.send_error(404)
            return None

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/ws":
                return self.handle_ws()
            if path == "/api/version":
                return self.send_json({"app": APP_ID, "version": VERSION})
            if path.startswith("/tiles/"):
                return self.handle_tile(path)
            return super().do_GET()

        def send_json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def handle_tile(self, path):
            m = re.fullmatch(r"/tiles/([a-z]+)/(\d{1,2})/(\d{1,8})/(\d{1,8})(?:\.\w+)?", path)
            data = bridge.tiles.get(m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))) if m else None
            if not data:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png")
            self.send_header("Content-Length", str(len(data)))
            self._custom_cache = True
            self.send_header("Cache-Control", "max-age=604800")
            self.end_headers()
            self.wfile.write(data)

        def handle_ws(self):
            key = self.headers.get("Sec-WebSocket-Key")
            if not key or "websocket" not in self.headers.get("Upgrade", "").lower():
                self.send_error(400)
                return
            accept = base64.b64encode(hashlib.sha1((key + WS_MAGIC).encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self._custom_cache = True
            self.end_headers()
            self.wfile.flush()
            sock = self.connection
            sock.settimeout(None)
            client = Client(sock, self.client_address)
            bridge.add_client(client)
            try:
                while client.alive:
                    op, payload = ws_read_message(self.rfile.read)
                    if op == 8:
                        client.send_raw(ws_frame(8, payload[:2]))
                        break
                    if op == 9:
                        client.send_raw(ws_frame(10, payload))
                    elif op == 1:
                        try:
                            msg = json.loads(payload.decode("utf-8"))
                        except ValueError:
                            continue
                        bridge.on_message(client, msg)
            except (ConnectionError, OSError):
                pass
            finally:
                bridge.remove_client(client)
                client.close()
                self.close_connection = True

    return Handler


# --------------------------------------------------------------------------------------
# the bridge
# --------------------------------------------------------------------------------------
class Bridge:
    def __init__(self, cfg, project_path=None, tile_root=None, config_path=None):
        self.cfg = cfg
        self.config_path = config_path or CONFIG_PATH
        self.lock = threading.RLock()
        self.clients = set()
        self.store = ProjectStore(project_path)
        self.tiles = TileCache(tile_root, cfg.get("tiles"))
        self.source = None
        self.ntrip = None
        self.last_gga_line = None
        self.last_fix = None
        self.started = time.time()
        self.server = None
        self.lan = []
        self.nmea_log = None
        self.probe_busy = False

    # ---- lifecycle --------------------------------------------------------------
    def start_server(self, host, port):
        self.server = Server((host, port), make_handler(self))
        threading.Thread(target=self.server.serve_forever, daemon=True, name="http").start()
        threading.Thread(target=self._status_loop, daemon=True, name="status").start()
        self.lan = lan_ips() if host != "127.0.0.1" else []

    def start_io(self):
        try:
            self._start_io()
        except Exception as e:
            log("could not start with these settings: %s" % e, "bridge")

    def _start_io(self):
        src = self.cfg["source"]
        t = src.get("type", "serial")
        if t == "serial":
            try:
                import serial  # noqa: F401
            except ImportError:
                log("pyserial is not installed — run:  pip install pyserial", "rx")
                self.source = None
            else:
                self.source = SerialSource(self, src.get("port") or "auto", int(src.get("baud") or 115200))
        elif t == "tcp":
            self.source = TcpSource(self, src.get("host"), int(src.get("tcpPort") or 0))
        elif t == "test":
            self.source = TestSource(self, src.get("center"))
        if self.source:
            self.source.start()
        n = self.cfg["ntrip"]
        if n.get("enabled") and n.get("host") and n.get("mount"):
            self.ntrip = NtripClient(self, n)
            self.ntrip.start()

    def stop_io(self):
        for t in (self.ntrip, self.source):
            if t:
                t.stop()
        for t in (self.ntrip, self.source):
            if t:
                t.join(timeout=3)
        self.ntrip = self.source = None

    def restart_io(self):
        self.stop_io()
        self.start_io()

    def shutdown(self):
        self.stop_io()
        for c in list(self.clients):
            c.close()
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def remember_port(self, port):
        self.cfg["source"]["port"] = port
        try:
            save_config(self.cfg, self.config_path)
        except OSError:
            pass

    # ---- data flow --------------------------------------------------------------
    def on_nmea(self, lines):
        for ln in lines:
            if ln[3:6] == "GGA":
                info = gga_info(ln)
                if info and info["lat"] is not None and info["q"] > 0:
                    self.last_gga_line = ln
                if info:
                    self.last_fix = info
        if self.cfg.get("logNmea"):
            self._log_nmea(lines)
        self.broadcast({"t": "nmea", "l": lines})

    def _log_nmea(self, lines):
        try:
            day = time.strftime("%Y%m%d")
            if not self.nmea_log or self.nmea_log[0] != day:
                os.makedirs(os.path.join(DATA_DIR, "logs"), exist_ok=True)
                self.nmea_log = (day, open(os.path.join(DATA_DIR, "logs", day + ".nmea"), "a", encoding="ascii"))
            self.nmea_log[1].write("\r\n".join(lines) + "\r\n")
            self.nmea_log[1].flush()
        except OSError:
            pass

    def write_to_receiver(self, data):
        s = self.source
        return s.write(data) if s else False

    # ---- clients ----------------------------------------------------------------
    def add_client(self, c):
        with self.lock:
            self.clients.add(c)
        c.send({"t": "hello", "app": APP_ID, "version": VERSION, "local": c.local,
                "lan": ["http://%s:%d/" % (ip, self.cfg["web"]["port"]) for ip in self.lan]})
        c.send({"t": "config", "cfg": public_config(self.cfg)})
        snap = self.store.snapshot()
        c.send({"t": "state", "rev": snap["rev"], "state": snap})
        c.send(self.status())

    def remove_client(self, c):
        with self.lock:
            self.clients.discard(c)

    def broadcast(self, obj):
        with self.lock:
            cl = list(self.clients)
        for c in cl:
            c.send(obj)

    def status(self):
        s = self.source
        return {"t": "status", "rx": s.status() if s else {"type": self.cfg["source"].get("type"), "state": "off"},
                "ntrip": self.ntrip.status() if self.ntrip else None, "fix": self.last_fix,
                "viewers": len(self.clients), "upSec": int(time.time() - self.started)}

    def _status_loop(self):
        while True:
            time.sleep(1)
            if self.clients:
                self.broadcast(self.status())

    def on_message(self, c, msg):
        t, rid = msg.get("t"), msg.get("id")

        def reply(obj):
            obj["id"] = rid
            c.send(obj)

        try:
            if t == "op":
                rev = self.store.apply(msg.get("op"))
                snap = self.store.snapshot()
                self.broadcast({"t": "state", "rev": rev, "state": snap})
                reply({"t": "ack", "rev": rev})
            elif t == "replaceState":
                rev = self.store.replace(msg.get("state"), only_if_empty=msg.get("onlyIfEmpty", True))
                self.broadcast({"t": "state", "rev": rev, "state": self.store.snapshot()})
                reply({"t": "ack", "rev": rev})
            elif t == "setConfig":
                if not c.local:
                    raise OpError("settings can only be changed on the laptop running the bridge")
                self.apply_config(msg.get("cfg") or {})
                self.broadcast({"t": "config", "cfg": public_config(self.cfg)})
                reply({"t": "ack"})
            elif t == "listPorts":
                reply({"t": "ports", "ports": list_ports()})
            elif t == "findBases":
                def job():
                    try:
                        rows = find_bases(msg.get("caster") or "rtk2go.com:2101", float(msg["lat"]), float(msg["lon"]))
                        reply({"t": "bases", "caster": msg.get("caster") or "rtk2go.com:2101", "rows": rows})
                    except Exception as e:
                        reply({"t": "nack", "msg": "could not read the caster's list: %s" % e})
                threading.Thread(target=job, daemon=True).start()
            elif t == "checkUpdate":
                def job():
                    reply({"t": "update", **check_update(self.cfg["update"]["branch"])})
                threading.Thread(target=job, daemon=True).start()
            elif t == "doUpdate":
                if not c.local:
                    raise OpError("updates can only be started on the laptop")

                def job():
                    try:
                        res = do_update(self.cfg["update"]["branch"])
                        reply({"t": "updated", **res})
                    except Exception as e:
                        reply({"t": "nack", "msg": "update failed: %s" % e})
                threading.Thread(target=job, daemon=True).start()
            elif t == "getLog":
                reply({"t": "log", "lines": list(LOG)[-200:]})
            elif t == "ping":
                reply({"t": "pong"})
            else:
                reply({"t": "nack", "msg": "unknown message %s" % t})
        except (OpError, ValueError, KeyError) as e:
            reply({"t": "nack", "msg": str(e)})

    def apply_config(self, new):
        cfg = deep_merge(self.cfg, new)
        nt = new.get("ntrip") or {}
        if nt.get("clearPass"):
            cfg["ntrip"]["pass"] = ""
        elif "ntrip" in new and not nt.get("pass"):
            cfg["ntrip"]["pass"] = self.cfg["ntrip"].get("pass", "")        # blank means "keep the saved one"
        cfg["ntrip"].pop("passSet", None)
        cfg["ntrip"].pop("clearPass", None)
        io_changed = cfg["source"] != self.cfg["source"] or cfg["ntrip"] != self.cfg["ntrip"]
        self.cfg = cfg
        save_config(cfg, self.config_path)
        log("settings saved", "bridge")
        if io_changed:
            threading.Thread(target=self.restart_io, daemon=True).start()

    # ---- console heartbeat ------------------------------------------------------
    def heartbeat(self):
        s = self.source
        if s:
            st = s.status()
            f = self.last_fix or {}
            if st["state"] == "open" and f:
                rx = "%s  %s  %s sats  hdop %s  (%s sentences/s)" % (
                    getattr(s, "port", s.label), FIX_NAMES.get(f.get("q"), "q%s" % f.get("q")), f.get("sats"),
                    f.get("hdop"), st["nmeaPerSec"])
            else:
                rx = "%s: %s" % (st["state"], st["detail"])
        else:
            rx = "no receiver source"
        line = "[rx] " + rx
        if self.ntrip:
            n = self.ntrip.status()
            line += "   [ntrip] %s %s %s B/s%s" % (n["mount"], n["state"], n["bytesPerSec"],
                                                 "  base %.1f km" % n["base"]["distKm"] if n.get("base") and n["base"].get("distKm") is not None else "")
        if self.clients:
            line += "   [%d viewer%s]" % (len(self.clients), "" if len(self.clients) == 1 else "s")
        log(line, "status")


# --------------------------------------------------------------------------------------
# helpers: network, single instance, browser, update, install
# --------------------------------------------------------------------------------------
def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))           # no packet is sent; picks the outward interface
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(ip)
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))


def running_instance(port):
    """'ours' if a Parcel RTK bridge answers on the port, 'other' if something else does, None if free"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/version" % port, timeout=1.5) as r:
            d = json.loads(r.read().decode())
            return "ours" if d.get("app") == APP_ID else "other"
    except urllib.error.HTTPError:
        return "other"
    except Exception:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return "other"
        except OSError:
            return None


# Chromium browsers that can open a clean "app" window, by the ProgId Windows uses for them
BROWSER_EXES = {
    "MSEdgeHTM": (r"Microsoft\Edge\Application\msedge.exe",),
    "ChromeHTML": (r"Google\Chrome\Application\chrome.exe",),
    "BraveHTML": (r"BraveSoftware\Brave-Browser\Application\brave.exe",),
}


def default_browser_exe(progid=None, exists=os.path.isfile):
    """The user's default browser if it can make an app window (Edge, Chrome, Brave), else None.
    Always the DEFAULT browser: the app keeps a copy of its data in the browser, and the old
    version opened in the default one, so a different browser would start out empty."""
    if progid is None:
        if os.name != "nt":
            return None
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice") as k:
                progid = winreg.QueryValueEx(k, "ProgId")[0]
        except OSError:
            return None
    rels = next((v for k, v in BROWSER_EXES.items() if progid.startswith(k)), None)
    if not rels:
        return None
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("LOCALAPPDATA", "")]
    for rel in rels:
        for root in roots:
            exe = os.path.join(root, rel)
            if root and exists(exe):
                return exe
    return None


def open_app(url, app_window=True):
    exe = default_browser_exe() if app_window else None
    if exe:
        try:
            subprocess.Popen([exe, "--app=" + url], close_fds=True)
            return
        except OSError:
            pass
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check_update(branch):
    try:
        src = _http_get("https://raw.githubusercontent.com/%s/%s/rtk_bridge.py" % (REPO, branch), 20).decode("utf-8", "ignore")
        m = re.search(r'^VERSION = "([^"]+)"', src, re.M)
        latest = m.group(1) if m else None

        def key(v):
            return tuple(int(x) for x in re.findall(r"\d+", v or "0"))
        return {"current": VERSION, "latest": latest, "available": bool(latest and key(latest) > key(VERSION)), "branch": branch}
    except Exception as e:
        return {"current": VERSION, "latest": None, "available": False, "error": str(e), "branch": branch}


PROTECTED = ("rtk_config.json", "rtk_data")


def install_zip(zbytes, dest):
    """copy the repo files from a GitHub zip into dest; never touches config or data"""
    changed, added = [], []
    with zipfile.ZipFile(io.BytesIO(zbytes)) as z:
        names = z.namelist()
        top = names[0].split("/", 1)[0] + "/"
        for name in names:
            if not name.startswith(top) or name.endswith("/"):
                continue
            rel = name[len(top):]
            parts = rel.split("/")
            if not rel or parts[0] in PROTECTED or ".." in parts or parts[0].startswith(".git"):
                continue
            target = os.path.join(dest, *parts)
            data = z.read(name)
            old = None
            if os.path.exists(target):
                with open(target, "rb") as f:
                    old = f.read()
            if old == data:
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + ".new"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
            (changed if old is not None else added).append(rel)
    return changed, added


def do_update(branch, dest=HERE):
    url = "https://codeload.github.com/%s/zip/refs/heads/%s" % (REPO, branch)
    log("downloading %s" % url, "update")
    data = _http_get(url, 120)
    changed, added = install_zip(data, dest)
    log("updated %d files, added %d. Close and reopen Parcel RTK to finish." % (len(changed), len(added)), "update")
    return {"changed": changed, "added": added, "restart": True}


def desktop_dir():
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                return os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0])
        except OSError:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def install_launcher():
    d = desktop_dir()
    if os.name == "nt":
        path = os.path.join(d, "Parcel RTK.bat")
        text = ("@echo off\r\ntitle Parcel RTK\r\ncd /d \"%s\"\r\n\"%s\" rtk_bridge.py\r\n"
                "if errorlevel 1 pause\r\n" % (HERE, sys.executable))
    else:
        path = os.path.join(d, "parcel-rtk.sh")
        text = "#!/bin/sh\ncd \"%s\" && exec \"%s\" rtk_bridge.py\n" % (HERE, sys.executable)
    os.makedirs(d, exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(text)
    if os.name != "nt":
        os.chmod(path, 0o755)
    return path


# --------------------------------------------------------------------------------------
def main(argv=None):
    setup_console()
    ap = argparse.ArgumentParser(description="Parcel RTK bridge %s" % VERSION)
    ap.add_argument("--source")
    ap.add_argument("--ntrip")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--port", type=int)
    ap.add_argument("--web-port", type=int, dest="port2", help=argparse.SUPPRESS)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-lan", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--find-bases", metavar="LAT,LON")
    ap.add_argument("--caster", default="rtk2go.com:2101")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--version", action="version", version=VERSION)
    ap.add_argument("--gga", help=argparse.SUPPRESS)                 # v1: seed GGA for the caster
    ap.add_argument("--no-web", action="store_true", help=argparse.SUPPRESS)   # v1: the app is always served now
    a = ap.parse_args(argv)

    if a.list:
        ports = list_ports()
        if ports is None:
            print("pyserial is not installed. Run:  pip install pyserial")
            return 1
        if not ports:
            print("No serial ports found.")
        for p in ports:
            tag = {"bt-out": "Bluetooth (outgoing - use this one)", "bt-in": "Bluetooth (incoming - not this one)",
                   "usb": "USB", "other": ""}[p["kind"]]
            print("  %-8s %-45s %s" % (p["device"], p["description"], tag))
        return 0
    if a.find_bases:
        la, lo = (float(x) for x in a.find_bases.split(","))
        print("nearest mountpoints on %s to %.5f,%.5f:" % (a.caster, la, lo))
        for r in find_bases(a.caster, la, lo, 15):
            flag = "  <-- great" if r["km"] <= 15 else ("  <-- usable" if r["km"] <= 35 else "")
            print("  %7.1f km  %-24s %-14s %s%s" % (r["km"], r["mount"], r["format"][:14], r["country"], flag))
        print("A ZED-F9P wants a base within ~15 km (35 km at most). Network services (PointPerfect NEAR-RTCM) don't need one.")
        return 0
    if a.update:
        cfg, _ = load_config()
        info = check_update(cfg["update"]["branch"])
        print("installed %s, latest %s" % (info["current"], info.get("latest")))
        do_update(cfg["update"]["branch"])
        return 0
    if a.install:
        print("created launcher: %s" % install_launcher())
        return 0

    cfg, existed = load_config()
    cli = {}
    if a.source:
        cli["source"] = parse_source(a.source)
    if a.ntrip:
        n = parse_ntrip_url(a.ntrip)
        n["enabled"] = True
        cli["ntrip"] = n
    if a.port or a.port2:
        cli.setdefault("web", {})["port"] = a.port or a.port2
    if a.no_lan:
        cli.setdefault("web", {})["lan"] = False
    if a.no_browser:
        cli.setdefault("web", {})["openBrowser"] = False
    if existed and cli:
        merged = deep_merge(cfg, cli)
        if merged["source"] != cfg["source"] or merged["ntrip"] != cfg["ntrip"]:
            print("Note: --source/--ntrip on the command line override rtk_config.json. Changes made in the app's "
                  "Settings are saved, but this launcher will override them next time - remove those options from it "
                  "(or run once with --save).")
    cfg = deep_merge(cfg, cli)
    first_time = not existed
    if a.save or (first_time and (a.source or a.ntrip)):
        save_config(cfg)
        print("Saved your settings to rtk_config.json — next time just run:  python rtk_bridge.py")
    elif first_time:
        save_config(cfg)

    port = int(cfg["web"]["port"])
    url = "http://localhost:%d/" % port
    inst = running_instance(port)
    if inst == "ours":
        print("Parcel RTK is already running — opening it. (Close the other window to restart it.)")
        if cfg["web"].get("openBrowser", True):
            open_app(url, cfg["web"].get("appWindow", True))
        return 0
    if inst == "other":
        print("Port %d is used by another program. Close it, or run with --port 8001." % port)
        return 2

    bridge = Bridge(cfg)
    if a.gga and nmea_ok(a.gga.strip()):
        bridge.last_gga_line = a.gga.strip()                       # lets a network caster start before the first fix
    host = "0.0.0.0" if cfg["web"].get("lan", True) else "127.0.0.1"
    try:
        bridge.start_server(host, port)
    except OSError as e:
        print("Could not start the web server on port %d: %s" % (port, e))
        return 2
    print("Parcel RTK %s" % VERSION)
    print("  app:    %s" % url)
    for ip in bridge.lan:
        print("  phone:  http://%s:%d/   (same Wi-Fi; allow Python through the Windows firewall if asked)" % (ip, port))
    bridge.start_io()
    if cfg["web"].get("openBrowser", True):
        open_app(url, cfg["web"].get("appWindow", True))
    try:
        while True:
            time.sleep(5)
            bridge.heartbeat()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        bridge.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
