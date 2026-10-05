#!/usr/bin/env python3
"""
Tests for rtk_bridge.py.   python -m unittest discover -s tests -v
Needs pyserial for the serial (pty) test; everything else is stdlib. No network.
"""
import base64
import io
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import rtk_bridge as rb  # noqa: E402

rb.log = lambda msg, tag="t": rb.LOG.append("[%s] %s" % (tag, msg))       # quiet

# Never touch the real rtk_config.json / rtk_data next to the bridge (they hold the
# user's NTRIP login and project): point every default path at a temp folder.
REAL_CONFIG = rb.CONFIG_PATH
_TMP = tempfile.mkdtemp(prefix="prtk-test-")
rb.CONFIG_PATH = os.path.join(_TMP, "rtk_config.json")
rb.DATA_DIR = os.path.join(_TMP, "rtk_data")
rb.PROJECT_PATH = os.path.join(rb.DATA_DIR, "project.json")
rb.TILE_DIR = os.path.join(rb.DATA_DIR, "tiles")


def wait_for(pred, timeout=5.0, step=0.02):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(step)
    return False


# ------------------------------------------------------------------------------- ops
class TestOps(unittest.TestCase):
    def partial(self, obj, exp, where=""):
        for k, v in exp.items():
            p = where + "." + k
            if isinstance(v, dict) and v:
                self.assertIsInstance(obj.get(k), dict, p)
                self.partial(obj[k], v, p)
            else:
                self.assertEqual(obj.get(k), v, p)

    def test_shared_vectors(self):
        with open(os.path.join(HERE, "ops_vectors.json")) as f:
            vectors = json.load(f)
        for v in vectors:
            with self.subTest(v["name"]):
                s, err = rb.empty_state(), None
                try:
                    for op in v["ops"]:
                        s = rb.apply_op(s, op)
                except rb.OpError as e:
                    err = e
                if v.get("error"):
                    self.assertIsNotNone(err, "expected an error")
                else:
                    self.assertIsNone(err, str(err))
                    self.partial(s, v["expect"])

    def test_store_persists_and_recovers_from_corruption(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "project.json")
        st = rb.ProjectStore(p)
        st.apply({"op": "push", "path": ["savedPoints"], "value": {"id": "1", "note": "gate"}})
        st.apply({"op": "set", "path": ["settings", "hold"], "value": False})
        self.assertEqual(rb.ProjectStore(p).snapshot()["savedPoints"][0]["note"], "gate")
        with open(p, "w") as f:
            f.write("{ this is not json")
        rec = rb.ProjectStore(p).snapshot()                       # falls back to project.json.bak
        self.assertEqual(rec["savedPoints"][0]["note"], "gate")

    def test_replace_only_when_empty(self):
        d = tempfile.mkdtemp()
        st = rb.ProjectStore(os.path.join(d, "p.json"))
        st.replace({"savedPoints": [{"id": "a"}], "control": {}, "rough": None})
        with self.assertRaises(rb.OpError):
            st.replace({"savedPoints": [{"id": "b"}]})
        self.assertEqual(st.snapshot()["savedPoints"], [{"id": "a"}])


# ------------------------------------------------------------------------------ parsing
class TestParsing(unittest.TestCase):
    def test_nmea_checksum(self):
        good = rb.nmea_with_checksum("GNGGA,173501.00,3933.0726,N,10517.9222,W,4,18,0.55,2655.1,M,-15.4,M,1.0,0000")
        self.assertTrue(rb.nmea_ok(good))
        self.assertFalse(rb.nmea_ok(good.replace("3933", "3934")))
        self.assertFalse(rb.nmea_ok("$GNGGA,1,2,3"))
        self.assertEqual(rb.gga_info(good)["q"], 4)

    def test_ntrip_urls_with_awkward_passwords(self):
        for url, pw in [("ntrip://AbCdEfGh1234:9Xy&qrT55@ppntrip.services.u-blox.com:2101/NEAR-RTCM", "9Xy&qrT55"),
                        ("ntrip://u:a^b@h:2101/M", "a^b"), ("ntrip://u:p@ss:w/rd@h:2101/M", "p@ss:w/rd"),
                        ("ntrip://you@mail.com:none@rtk2go.com:2101/Base", "none")]:
            n = rb.parse_ntrip_url(url)
            self.assertEqual(n["pass"], pw)
        n = rb.parse_ntrip_url("ntrip://AbCdEfGh1234:9Xy&qrT55@ppntrip.services.u-blox.com:2101/NEAR-RTCM")
        self.assertEqual((n["user"], n["host"], n["port"], n["mount"]), ("AbCdEfGh1234", "ppntrip.services.u-blox.com", 2101, "NEAR-RTCM"))

    def test_default_browser_app_window(self):
        found = lambda path: path.endswith(("chrome.exe", "msedge.exe"))
        self.assertTrue(rb.default_browser_exe("ChromeHTML", found).endswith("chrome.exe"))
        self.assertTrue(rb.default_browser_exe("MSEdgeHTM", found).endswith("msedge.exe"))
        self.assertIsNone(rb.default_browser_exe("FirefoxURL-308046B0AF4A39CB", found))   # -> plain webbrowser.open
        self.assertIsNone(rb.default_browser_exe("BraveHTML", found))                    # not installed

    def test_sources(self):
        self.assertEqual(rb.parse_source("serial:COM8:115200"), {"type": "serial", "port": "COM8", "baud": 115200})
        self.assertEqual(rb.parse_source("serial:/dev/ttyUSB0")["port"], "/dev/ttyUSB0")
        self.assertEqual(rb.parse_source("auto")["port"], "auto")
        self.assertEqual(rb.parse_source("test:39.5,-105.3")["center"], [39.5, -105.3])

    def test_bluetooth_port_direction(self):
        out = r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002\7&1C2F4A55&0&B8D61A2B3C4D_C00000000"
        inc = r"BTHENUM\{00001101-0000-1000-8000-00805F9B34FB}_LOCALMFG&0000\7&1C2F4A55&0&000000000000_00000000"
        self.assertEqual(rb.classify_port("COM8", "Standard Serial over Bluetooth link (COM8)", out), "bt-out")
        self.assertEqual(rb.classify_port("COM7", "Standard Serial over Bluetooth link (COM7)", inc), "bt-in")
        self.assertEqual(rb.classify_port("COM3", "USB-SERIAL CH340 (COM3)", "USB VID:PID=1A86:7523"), "usb")

    def test_public_config_hides_password(self):
        cfg = rb.deep_merge(rb.DEFAULT_CONFIG, {"ntrip": {"pass": "secret"}})
        pub = rb.public_config(cfg)
        self.assertEqual(pub["ntrip"]["pass"], "")
        self.assertTrue(pub["ntrip"]["passSet"])
        self.assertEqual(cfg["ntrip"]["pass"], "secret")

    def test_find_bases_parsing(self):
        st = ("SOURCETABLE 200 OK\r\n"
              "STR;NEAR;Conifer;RTCM 3.2;1005(1);2;GPS;SNIP;USA;39.56;-105.30;1;0;sNTRIP;none;B;N;0;;\r\n"
              "STR;FAR;Denver;RTCM 3.2;1005(1);2;GPS;SNIP;USA;39.74;-104.99;0;0;sNTRIP;none;B;N;0;;\r\n"
              "STR;BAD;x;y;z;2;GPS;SNIP;USA;notanumber;x;0\r\nENDSOURCETABLE\r\n")
        rows = rb.find_bases("x:2101", 39.55, -105.30, text=st)
        self.assertEqual([r["mount"] for r in rows], ["NEAR", "FAR"])
        self.assertTrue(rows[0]["needsGga"])


# --------------------------------------------------------------------------------- RTCM
class TestRtcm(unittest.TestCase):
    def test_1005_roundtrip_and_resync(self):
        x, y, z = rb.geo_to_ecef(39.55, -105.30, 2640.0)
        frame = rb.encode_rtcm(rb.make_1005(3210, x, y, z))
        msm = rb.encode_rtcm(bytes([0x43, 0x20]) + b"\x00" * 30)          # type 1074
        sc = rb.RtcmScanner()
        stream = b"\x00\xff garbage \xd3\x00" + frame + msm + frame
        for i in range(0, len(stream), 7):                                 # arbitrary split points
            sc.feed(stream[i:i + 7])
        self.assertEqual(sc.counts[1005], 2)
        self.assertEqual(sc.counts[1074], 1)
        self.assertEqual(sc.base["station"], 3210)
        self.assertAlmostEqual(sc.base["lat"], 39.55, places=7)
        self.assertAlmostEqual(sc.base["lon"], -105.30, places=7)
        self.assertEqual(sc.describe(), "RTCM3")

    def test_bad_crc_is_rejected(self):
        frame = bytearray(rb.encode_rtcm(rb.make_1005(1, 1e6, 1e6, 1e6)))
        frame[10] ^= 0x01
        sc = rb.RtcmScanner()
        sc.feed(bytes(frame))
        self.assertEqual(sc.frames, 0)
        self.assertGreaterEqual(sc.bad, 1)

    def test_spartn_detected(self):
        sc = rb.RtcmScanner()
        sc.feed(b"\x73" + os.urandom(2000).replace(b"\xd3", b"\x00"))
        self.assertEqual(sc.describe(), "SPARTN")

    def test_dechunker(self):
        payload = os.urandom(5000)
        enc = b""
        for i in range(0, len(payload), 777):
            part = payload[i:i + 777]
            enc += b"%X\r\n" % len(part) + part + b"\r\n"
        enc += b"0\r\n\r\n"
        d, out = rb.Dechunker(), b""
        for i in range(0, len(enc), 13):
            out += d.feed(enc[i:i + 13])
        self.assertEqual(out, payload)


# ------------------------------------------------------------------------------- NTRIP
class FakeBridge:
    def __init__(self):
        self.received = bytearray()
        self.last_gga_line = rb.nmea_with_checksum("GNGGA,173501.00,3933.0726,N,10517.9222,W,4,18,0.55,2655.1,M,-15.4,M,1.0,0000")
        self.last_fix = {"lat": 39.551, "lon": -105.299}
        self.lines = []

    def write_to_receiver(self, data):
        self.received += data
        return True

    def on_nmea(self, lines):
        self.lines.extend(lines)

    def remember_port(self, p):
        pass


class MockCaster(threading.Thread):
    def __init__(self, head, body=b"", chunked=False, hold=1.5):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.head, self.body, self.chunked, self.hold = head, body, chunked, hold
        self.request, self.after = b"", b""
        self.start()

    def run(self):
        c, _ = self.srv.accept()
        c.settimeout(3)
        while b"\r\n\r\n" not in self.request:
            self.request += c.recv(1)
        c.sendall(self.head)
        data = self.body
        for i in range(0, len(data), 300):
            part = data[i:i + 300]
            c.sendall(b"%X\r\n" % len(part) + part + b"\r\n" if self.chunked else part)
            time.sleep(0.01)
        t0 = time.time()
        c.settimeout(0.2)
        while time.time() - t0 < self.hold:
            try:
                got = c.recv(4096)
                if not got:
                    break
                self.after += got
            except socket.timeout:
                pass
        c.close()


def rtcm_stream():
    x, y, z = rb.geo_to_ecef(39.552, -105.300, 2640.0)
    out = b""
    for _ in range(10):
        out += rb.encode_rtcm(rb.make_1005(7, x, y, z)) + rb.encode_rtcm(bytes([0x43, 0x20]) + os.urandom(40))
    return out


class TestNtrip(unittest.TestCase):
    def run_client(self, caster, mount="NEAR-RTCM", until=None):
        fb = FakeBridge()
        cl = rb.NtripClient(fb, {"host": "127.0.0.1", "port": caster.port, "mount": mount, "user": "u", "pass": "p&^x", "ggaInterval": 2})
        cl.start()
        wait_for(until or (lambda: False), 4)
        st = cl.status()
        cl.stop()
        caster.join(3)
        return fb, cl, st

    def test_v1_icy(self):
        body = rtcm_stream()
        cas = MockCaster(b"ICY 200 OK\r\n", body)
        fb, cl, st = self.run_client(cas, until=lambda: False)
        self.assertEqual(bytes(fb.received), body)
        req = cas.request.decode()
        self.assertIn("GET /NEAR-RTCM HTTP/1.1", req)
        self.assertIn("Authorization: Basic " + base64.b64encode(b"u:p&^x").decode(), req)
        self.assertIn("Ntrip-GGA: $GNGGA", req)
        self.assertIn(b"$GNGGA", cas.after)                         # GGA also sent upstream after connecting
        self.assertEqual(st["format"], "RTCM3")
        self.assertIn(1005, st["types"])
        self.assertLess(st["base"]["distKm"], 0.5)

    def test_v2_chunked_is_dechunked(self):
        body = rtcm_stream()
        cas = MockCaster(b"HTTP/1.1 200 OK\r\nContent-Type: gnss/data\r\nTransfer-Encoding: chunked\r\n\r\n", body, chunked=True)
        fb, cl, st = self.run_client(cas)
        self.assertEqual(bytes(fb.received), body)                  # chunk headers never reach the receiver
        self.assertEqual(st["badFrames"], 0)

    def test_bad_password(self):
        cas = MockCaster(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n", hold=0.2)
        fb, cl, st = self.run_client(cas, until=lambda: False)
        self.assertEqual(st["state"], "refused")
        self.assertIn("password", st["detail"])

    def test_unknown_mountpoint(self):
        cas = MockCaster(b"SOURCETABLE 200 OK\r\nContent-Type: text/plain\r\n\r\nENDSOURCETABLE\r\n", hold=0.2)
        fb, cl, st = self.run_client(cas, mount="NOPE")
        self.assertEqual(st["state"], "refused")
        self.assertIn("NOPE", st["detail"])

    def test_spartn_warning(self):
        rb.LOG.clear()
        cas = MockCaster(b"ICY 200 OK\r\n", b"\x73" + os.urandom(6000).replace(b"\xd3", b"\x01"))
        self.run_client(cas)
        self.assertTrue(any("SPARTN" in l for l in rb.LOG), list(rb.LOG))


# ------------------------------------------------------------------- HTTP + WebSocket
class WS:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET /ws HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % key).encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.s.recv(1)
        self.head = resp.decode()
        self.inbox = []

    def send(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        hdr = struct.pack("!BB", 0x81, 0x80 | n) if n < 126 else struct.pack("!BBH", 0x81, 0x80 | 126, n)
        self.s.sendall(hdr + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(data)))

    def recv(self):
        op, payload = rb.ws_read_message(lambda n: self.s.recv(n))
        return json.loads(payload) if op == 1 else None

    def until(self, pred, timeout=5):
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = self.recv()
            if m is not None:
                self.inbox.append(m)
                if pred(m):
                    return m
        raise AssertionError("no matching message; got types %s" % [m.get("t") for m in self.inbox])


class TestCli(unittest.TestCase):
    def test_v1_launcher_options_still_parse(self):
        # the old start_rtk.bat style command line must keep working (it is saved on first run)
        seen = {}
        real = (rb.Bridge, rb.running_instance)
        class Stop(Exception):
            pass
        def fake_bridge(cfg, **kw):
            seen["cfg"] = cfg
            raise Stop()
        rb.Bridge, rb.running_instance = fake_bridge, (lambda port: None)
        try:
            with self.assertRaises(Stop):
                rb.main(["--source", "serial:COM8:115200", "--ntrip", "ntrip://AbCdEfGh1234:9Xy&qrT55@ppntrip.services.u-blox.com:2101/NEAR-RTCM",
                         "--no-browser", "--no-web", "--gga", "$GPGGA,x*00"])
        finally:
            rb.Bridge, rb.running_instance = real
        c = seen["cfg"]
        self.assertEqual((c["source"]["port"], c["ntrip"]["mount"], c["ntrip"]["pass"]), ("COM8", "NEAR-RTCM", "9Xy&qrT55"))
        with open(rb.CONFIG_PATH) as f:                                  # first run: saved (to the temp config)
            self.assertEqual(json.load(f)["ntrip"]["user"], "AbCdEfGh1234")
        os.remove(rb.CONFIG_PATH)


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cfg = rb.deep_merge(rb.DEFAULT_CONFIG, {"source": {"type": "test"}, "ntrip": {"pass": "topsecret", "host": "", "enabled": False},
                                                "web": {"port": 0}})
        cls.cfg_path = os.path.join(cls.tmp, "rtk_config.json")
        cls.bridge = rb.Bridge(cfg, project_path=os.path.join(cls.tmp, "project.json"), tile_root=os.path.join(cls.tmp, "tiles"),
                               config_path=cls.cfg_path)
        cls.bridge.start_server("127.0.0.1", 0)
        cls.port = cls.bridge.server.server_address[1]
        cls.bridge.start_io()

    @classmethod
    def tearDownClass(cls):
        cls.bridge.shutdown()

    def get(self, path):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=5) as r:
                return r.status, r.headers, r.read()            # HTTPMessage: case-insensitive .get
        except urllib.error.HTTPError as e:
            return e.code, e.headers, b""

    def test_static_files_and_headers(self):
        code, h, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"<html", body.lower())
        self.assertEqual(h.get("Cache-Control"), "no-cache")
        code, h, _ = self.get("/js/cogo.js")
        self.assertEqual((code, h.get("Content-Type")), (200, "text/javascript"))

    def test_no_path_traversal_or_listing(self):
        for p in ("/../rtk_bridge.py", "/..%2frtk_bridge.py", "/js/", "/../rtk_data/project.json", "/%2e%2e/rtk_config.json"):
            code, _, body = self.get(p)
            self.assertNotEqual(code, 200, p)
            self.assertNotIn(b"topsecret", body)

    def test_version_and_single_instance(self):
        self.assertEqual(json.loads(self.get("/api/version")[2])["app"], "parcel-rtk")
        self.assertEqual(rb.running_instance(self.port), "ours")
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
        s.close()
        self.assertIsNone(rb.running_instance(free))

    def test_websocket_flow(self):
        a, b = WS(self.port), WS(self.port)
        self.assertIn("HTTP/1.1 101", a.head)
        hello = a.until(lambda m: m["t"] == "hello")
        self.assertTrue(hello["local"])
        cfg = a.until(lambda m: m["t"] == "config")["cfg"]
        self.assertEqual(cfg["ntrip"]["pass"], "")                       # password never sent
        self.assertNotIn("topsecret", json.dumps(cfg))
        a.until(lambda m: m["t"] == "state")
        a.until(lambda m: m["t"] == "nmea" and any("GGA" in l for l in m["l"]))     # live data from the test receiver
        a.send({"t": "op", "id": 1, "op": {"op": "push", "path": ["savedPoints"], "value": {"id": "p1", "note": "big rock"}}})
        ack = a.until(lambda m: m["t"] in ("ack", "nack") and m.get("id") == 1)
        self.assertEqual(ack["t"], "ack")
        st = b.until(lambda m: m["t"] == "state" and m["state"]["savedPoints"])     # the other viewer sees it
        self.assertEqual(st["state"]["savedPoints"][0]["note"], "big rock")
        a.send({"t": "op", "id": 2, "op": {"op": "set", "path": ["evil"], "value": 1}})
        self.assertEqual(a.until(lambda m: m.get("id") == 2)["t"], "nack")
        a.until(lambda m: m["t"] == "status" and m["rx"]["state"] == "open")
        a.s.close()
        b.s.close()

    def test_remote_viewer_cannot_change_settings(self):
        class C:
            local = False
            def __init__(self):
                self.out = []
            def send(self, o):
                self.out.append(o)
        c = C()
        self.bridge.on_message(c, {"t": "setConfig", "id": 9, "cfg": {"ntrip": {"host": "evil"}}})
        self.assertEqual(c.out[-1]["t"], "nack")
        self.assertNotEqual(self.bridge.cfg["ntrip"]["host"], "evil")

    def test_blank_password_keeps_saved_one(self):
        class C:
            local = True
            def send(self, o):
                pass
        real_before = os.path.getmtime(REAL_CONFIG) if os.path.exists(REAL_CONFIG) else None
        self.bridge.on_message(C(), {"t": "setConfig", "id": 1, "cfg": {"ntrip": {"mount": "X", "pass": ""}}})
        self.assertEqual(self.bridge.cfg["ntrip"]["pass"], "topsecret")
        self.assertEqual(self.bridge.cfg["ntrip"]["mount"], "X")
        with open(self.cfg_path) as f:                                   # saved to the bridge's own file ...
            self.assertEqual(json.load(f)["ntrip"]["mount"], "X")
        real_after = os.path.getmtime(REAL_CONFIG) if os.path.exists(REAL_CONFIG) else None
        self.assertEqual(real_before, real_after)                        # ... never to the real one
        self.bridge.on_message(C(), {"t": "setConfig", "id": 2, "cfg": {"ntrip": {"pass": "", "clearPass": True}}})
        self.assertEqual(self.bridge.cfg["ntrip"]["pass"], "")            # "forget the saved password"
        self.assertNotIn("clearPass", self.bridge.cfg["ntrip"])
        self.bridge.on_message(C(), {"t": "setConfig", "id": 3, "cfg": {"ntrip": {"pass": "topsecret"}}})
        self.assertEqual(self.bridge.cfg["ntrip"]["pass"], "topsecret")


# ------------------------------------------------------------------------- tile cache
class TestTiles(unittest.TestCase):
    def test_cache_then_serve_offline(self):
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 100
        hits = []

        class H(rb.http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", str(len(png)))
                self.end_headers()
                self.wfile.write(png)

            def log_message(self, *a):
                pass
        up = rb.http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        root = tempfile.mkdtemp()
        tc = rb.TileCache(root, {"sat": "http://127.0.0.1:%d/{z}/{x}/{y}" % up.server_address[1]})
        self.assertEqual(tc.get("sat", 16, 13000, 24000), png)
        up.shutdown()
        up.server_close()
        self.assertEqual(tc.get("sat", 16, 13000, 24000), png)             # served from disk, upstream gone
        self.assertEqual(len(hits), 1)
        self.assertIsNone(tc.get("sat", 3, 99, 0))                         # out of range
        self.assertIsNone(tc.get("nope", 1, 0, 0))


# ------------------------------------------------------------------------------ updater
class TestUpdater(unittest.TestCase):
    def test_install_zip_never_touches_config_or_data(self):
        dest = tempfile.mkdtemp()
        with open(os.path.join(dest, "rtk_config.json"), "w") as f:
            f.write("MINE")
        with open(os.path.join(dest, "README.md"), "w") as f:
            f.write("old")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("repo-branch/README.md", "new")
            z.writestr("repo-branch/web/js/app.js", "js")
            z.writestr("repo-branch/rtk_config.json", "THEIRS")
            z.writestr("repo-branch/rtk_data/project.json", "{}")
            z.writestr("repo-branch/.github/x", "x")
        changed, added = rb.install_zip(buf.getvalue(), dest)
        self.assertEqual(changed, ["README.md"])
        self.assertEqual(added, ["web/js/app.js"])
        with open(os.path.join(dest, "rtk_config.json")) as f:
            self.assertEqual(f.read(), "MINE")
        self.assertFalse(os.path.exists(os.path.join(dest, "rtk_data")))


# ------------------------------------------------------------------- serial end-to-end
@unittest.skipUnless(hasattr(os, "openpty"), "needs a pty (Linux/macOS)")
class TestSerialPty(unittest.TestCase):
    def test_nmea_in_rtcm_out(self):
        try:
            import serial  # noqa: F401
        except ImportError:
            self.skipTest("pyserial not installed")
        master, slave = os.openpty()
        fb = FakeBridge()
        src = rb.SerialSource(fb, os.ttyname(slave), 115200)
        src.start()
        self.assertTrue(wait_for(lambda: src.state == "open"))
        good = rb.nmea_with_checksum("GNGGA,173501.00,3933.0726,N,10517.9222,W,4,18,0.55,2655.1,M,-15.4,M,1.0,0000")
        os.write(master, b"junk\r\n" + good.encode() + b"\r\n" + good.replace("3933", "3934").encode() + b"\r\n")
        self.assertTrue(wait_for(lambda: fb.lines == [good]))
        self.assertEqual(src.bad, 1)                                        # corrupted sentence dropped
        frame = rb.encode_rtcm(rb.make_1005(1, 1e6, 2e6, 3e6))
        self.assertTrue(src.write(frame))
        got = b""
        t0 = time.time()
        while len(got) < len(frame) and time.time() - t0 < 3:
            got += os.read(master, 4096)
        self.assertEqual(got, frame)
        src.stop()
        src.join(3)
        os.close(master)
        os.close(slave)


if __name__ == "__main__":
    unittest.main(verbosity=2)
