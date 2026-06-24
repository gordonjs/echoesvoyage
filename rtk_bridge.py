#!/usr/bin/env python3
"""
rtk_bridge.py  -  NMEA -> WebSocket bridge for the Parcel RTK web app.

Use this ONLY if you are NOT connecting the receiver by USB directly in the
browser (Web Serial). It is the recommended path for:

  * Bluetooth  - the SparkFun RTK pairs to Windows as an outgoing COM port,
                 then this bridge reads that COM port.
  * TCP / WiFi - the SparkFun RTK firmware can act as a TCP server that
                 streams NMEA; this bridge connects to it.

The bridge reads NMEA from the chosen source and relays every line to the
browser over a WebSocket. In the web app choose source = "Bridge (WS)" and
connect to  ws://localhost:8765

Only dependency is `pyserial`, and only when reading a serial/Bluetooth COM
port:   pip install pyserial
TCP and test sources need nothing but the Python standard library.

Examples
--------
List serial / Bluetooth COM ports:
    python rtk_bridge.py --list

Bluetooth or USB COM port (Windows):
    python rtk_bridge.py --source serial:COM5:38400

USB serial on Linux/Mac:
    python rtk_bridge.py --source serial:/dev/ttyUSB0:115200

TCP NMEA server (receiver's IP and port):
    python rtk_bridge.py --source tcp:192.168.4.1:2948

Synthetic data to test the browser app end to end:
    python rtk_bridge.py --source test
"""
import argparse, base64, hashlib, socket, struct, sys, threading, time, math

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
            # keep the socket; drain anything the client sends (pings/close)
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
        hdr = bytearray([0x81])  # FIN + text opcode
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
# Sources
# --------------------------------------------------------------------------
def run_serial(spec, hub):
    try:
        import serial  # pyserial
    except ImportError:
        sys.exit("pyserial not installed. Run:  pip install pyserial")
    _, port, *rest = spec.split(":")
    baud = int(rest[0]) if rest else 38400
    print(f"[src] opening serial {port} @ {baud}")
    ser = serial.Serial(port, baud, timeout=1)
    buf = b""
    while True:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                s = line.decode("ascii", "ignore").strip()
                if s:
                    hub.broadcast(s + "\n")


def run_tcp(spec, hub):
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


def nmea_checksum(body):
    c = 0
    for ch in body:
        c ^= ord(ch)
    return f"{c:02X}"


def run_test(hub):
    """Emit synthetic RTK-fixed GGA walking a small loop near Conifer, CO."""
    print("[src] TEST mode - synthetic NMEA")
    lat0, lon0 = 39.49700, -105.30500
    t = 0.0
    while True:
        lat = lat0 + 0.0006 * math.sin(t)
        lon = lon0 + 0.0006 * math.cos(t) * 0.78
        la = int(abs(lat)); lm = (abs(lat) - la) * 60
        lo = int(abs(lon)); om = (abs(lon) - lo) * 60
        body = (f"GPGGA,120000.00,{la:02d}{lm:08.5f},N,"
                f"{lo:03d}{om:08.5f},W,4,24,0.6,2400.0,M,-20.0,M,,")
        hub.broadcast(f"${body}*{nmea_checksum(body)}\n")
        t += 0.05
        time.sleep(0.2)


def main():
    ap = argparse.ArgumentParser(description="NMEA -> WebSocket bridge")
    ap.add_argument("--source", default="test",
                    help="serial:PORT[:BAUD] | tcp:HOST:PORT | test")
    ap.add_argument("--port", type=int, default=8765, help="WebSocket port")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    args = ap.parse_args()

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

    src = args.source
    try:
        if src.startswith("serial:"):
            run_serial(src, hub)
        elif src.startswith("tcp:"):
            run_tcp(src, hub)
        elif src == "test":
            run_test(hub)
        else:
            sys.exit(f"Unknown source: {src}")
    except KeyboardInterrupt:
        print("\n[bridge] stopped")


if __name__ == "__main__":
    main()
