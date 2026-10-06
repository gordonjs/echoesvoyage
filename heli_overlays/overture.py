"""Read OpenStreetMap-derived features from Overture Maps GeoParquet on S3.

Overture's base theme (land / water / infrastructure) is built from
OpenStreetMap and keeps the original OSM tags in `source_tags`. The files are
spatially sorted, so we read each file's footer, keep only row groups whose
bbox statistics overlap the area, and fetch just those byte ranges over HTTPS.
"""
import io
import re
import time

import pyarrow as pa
import pyarrow.parquet as pq
import requests

BUCKET = "https://overturemaps-us-west-2.s3.amazonaws.com"
S = requests.Session()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class RangeFile(io.RawIOBase):
    """Seekable read-only file over HTTP range requests, with a small block cache."""

    BLOCK = 8 << 20

    def __init__(self, url, size):
        self.url, self.size, self.pos, self.cache = url, size, 0, {}

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def _block(self, b):
        if b not in self.cache:
            lo, hi = b * self.BLOCK, min(self.size, (b + 1) * self.BLOCK) - 1
            for attempt in range(5):
                try:
                    r = S.get(self.url, headers={"Range": f"bytes={lo}-{hi}"}, timeout=300)
                    r.raise_for_status()
                    break
                except requests.RequestException:
                    if attempt == 4:
                        raise
                    time.sleep(2 ** (attempt + 1))
            if len(self.cache) > 16:
                self.cache.pop(next(iter(self.cache)))
            self.cache[b] = r.content
        return self.cache[b]

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        out = bytearray()
        while n > 0 and self.pos < self.size:
            b, off = divmod(self.pos, self.BLOCK)
            chunk = self._block(b)[off:off + n]
            out += chunk
            self.pos += len(chunk)
            n -= len(chunk)
        return bytes(out)

    def readinto(self, buf):
        data = self.read(len(buf))
        buf[:len(data)] = data
        return len(data)


def latest_release():
    t = S.get(f"{BUCKET}/?list-type=2&prefix=release/&delimiter=/", timeout=60).text
    return sorted(re.findall(r"<Prefix>release/([^<]+)/</Prefix>", t))[-1]


def list_files(release, otype):
    keys, token = [], None
    while True:
        q = f"{BUCKET}/?list-type=2&prefix=release/{release}/theme=base/type={otype}/"
        if token:
            q += f"&continuation-token={requests.utils.quote(token)}"
        t = S.get(q, timeout=60).text
        keys += [(k, int(s)) for k, s in
                 re.findall(r"<Key>([^<]+\.parquet)</Key>.*?<Size>(\d+)</Size>", t)]
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", t)
        if not m:
            return keys
        token = m.group(1)


def _bbox_cols(md):
    names = [md.schema.column(i).path for i in range(md.num_columns)]
    return {k: names.index(f"bbox.{k}") for k in ("xmin", "ymin", "xmax", "ymax")}


def read_area(release, otype, area, columns, row_filter=None):
    """All rows of base/<otype> whose bbox overlaps area=(W,S,E,N)."""
    W, Sth, E, N = area
    tables = []
    files = list_files(release, otype)
    log(f"  overture {otype}: {len(files)} files")
    for key, size in files:
        f = RangeFile(f"{BUCKET}/{key}", size)
        pf = pq.ParquetFile(pa.PythonFile(f, mode="r"))
        md = pf.metadata
        bc = _bbox_cols(md)
        groups = []
        for g in range(md.num_row_groups):
            rg = md.row_group(g)
            st = {k: rg.column(i).statistics for k, i in bc.items()}
            if any(s is None or not s.has_min_max for s in st.values()):
                groups.append(g)
                continue
            if st["xmin"].min <= E and st["xmax"].max >= W and \
               st["ymin"].min <= N and st["ymax"].max >= Sth:
                groups.append(g)
        if not groups:
            continue
        t = pf.read_row_groups(groups, columns=columns + ["bbox"])
        b = t.column("bbox").combine_chunks()
        xmin, ymin = b.field("xmin").to_numpy(), b.field("ymin").to_numpy()
        xmax, ymax = b.field("xmax").to_numpy(), b.field("ymax").to_numpy()
        keep = (xmin <= E) & (xmax >= W) & (ymin <= N) & (ymax >= Sth)
        t = t.filter(pa.array(keep))
        if row_filter is not None and t.num_rows:
            t = t.filter(row_filter(t))
        if t.num_rows:
            tables.append(t.drop(["bbox"]))
        log(f"    {key.rsplit('/', 1)[-1][:12]}: {len(groups)}/{md.num_row_groups} row groups, "
            f"{t.num_rows} rows kept")
    return pa.concat_tables(tables) if tables else None
