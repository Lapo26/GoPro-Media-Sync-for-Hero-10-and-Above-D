"""Lectura de metadatos: EXIF, MP4, fechas de captura, videos danados y GPS (GPMF)."""
import os
import struct
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from .i18n import _human_size, tr

# --- [ AUFNAHMEDATUM AUS DATEI-METADATEN LESEN ] --------------------------
#
# Fuer die Datums-Ordner (YYYY-MM-DD) beim Sync brauchen wir das ECHTE
# Aufnahmedatum der Datei, nicht das Kopierdatum. Das steckt in den
# Metadaten der Datei selbst:
#  - JPEG: im EXIF-Block (Tag "DateTimeOriginal")
#  - MP4:  in der "mvhd"-Box im "moov"-Container (Sekunden seit 1.1.1904)
#
# Beides wird hier bewusst OHNE Zusatz-Bibliothek (kein Pillow/ffprobe)
# geparst, passend zum Rest dieses Tools, das schon auf moeglichst wenige
# Abhaengigkeiten setzt. Bei Videos wird dabei NICHT die komplette (oft
# mehrere GB grosse) Datei eingelesen, sondern nur die kleinen Metadaten-
# Boxen - die eigentlichen Bilddaten ("mdat") werden nur ueberSPRUNGEN
# (geseekt), nie gelesen.

def _read_jpeg_exif_datetime(path):
    """Liest DateTimeOriginal (0x9003) bzw. ersatzweise DateTime (0x0132)
    aus dem EXIF-Block eines JPEGs. Gibt ein datetime-Objekt oder None
    zurueck (z.B. wenn die Datei kein EXIF hat oder kein JPEG ist)."""
    try:
        with open(path, "rb") as f:
            data = f.read(131072)  # EXIF sitzt immer nahe am Dateianfang

        if data[0:2] != b'\xff\xd8':
            return None

        pos = 2
        exif_data = None
        while pos + 4 <= len(data):
            if data[pos] != 0xFF:
                break
            marker = data[pos + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                pos += 2
                continue
            if marker == 0xDA:  # Start of Scan - danach kommt kein EXIF mehr
                break
            seg_len = (data[pos + 2] << 8) | data[pos + 3]
            if marker == 0xE1 and data[pos + 4:pos + 10] == b'Exif\x00\x00':
                exif_data = data[pos + 10: pos + 2 + seg_len]
                break
            pos += 2 + seg_len

        if not exif_data or len(exif_data) < 8:
            return None

        byte_order = exif_data[0:2]
        if byte_order == b'II':
            endian = '<'
        elif byte_order == b'MM':
            endian = '>'
        else:
            return None

        def read_ifd(offset):
            entries = {}
            if offset + 2 > len(exif_data):
                return entries
            count = struct.unpack(endian + 'H', exif_data[offset:offset + 2])[0]
            p = offset + 2
            type_sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
            for _ in range(count):
                if p + 12 > len(exif_data):
                    break
                tag, typ, cnt = struct.unpack(endian + 'HHI', exif_data[p:p + 8])
                raw_field = exif_data[p + 8:p + 12]
                size = type_sizes.get(typ, 1) * cnt
                if size <= 4:
                    raw = raw_field[:size]
                else:
                    off = struct.unpack(endian + 'I', raw_field)[0]
                    raw = exif_data[off:off + size]
                entries[tag] = raw
                p += 12
            return entries

        ifd0_offset = struct.unpack(endian + 'I', exif_data[4:8])[0]
        ifd0 = read_ifd(ifd0_offset)

        exif_sub = {}
        if 0x8769 in ifd0:
            sub_offset = struct.unpack(endian + 'I', ifd0[0x8769][:4])[0]
            exif_sub = read_ifd(sub_offset)

        for tag_set, tag in ((exif_sub, 0x9003), (exif_sub, 0x9004), (ifd0, 0x0132)):
            if tag in tag_set:
                text = tag_set[tag].split(b'\x00')[0].decode('ascii', errors='ignore').strip()
                try:
                    return datetime.strptime(text, "%Y:%m:%d %H:%M:%S")
                except Exception:
                    continue
        return None
    except Exception:
        return None


def _read_mp4_creation_time(path, assume_local=False):
    """Liest das Aufnahmedatum aus der 'mvhd'-Box im 'moov'-Container eines
    MP4/MOV. Liest dabei NUR die (kleinen) Metadaten-Boxen von der Platte,
    ueberspringt aber die riesige 'mdat'-Box per seek() statt sie zu lesen -
    funktioniert daher auch bei mehrere GB grossen GoPro-Videos schnell."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            f.seek(0)

            pos = 0
            moov_data = None
            while pos + 8 <= file_size:
                f.seek(pos)
                header = f.read(8)
                if len(header) < 8:
                    break
                box_size = struct.unpack(">I", header[0:4])[0]
                box_type = header[4:8]
                header_len = 8
                if box_size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        break
                    box_size = struct.unpack(">Q", ext)[0]
                    header_len = 16
                elif box_size == 0:
                    box_size = file_size - pos
                if box_size < header_len:
                    break
                if box_type == b'moov':
                    f.seek(pos)
                    # Sicherheitsobergrenze - 'moov' ist bei GoPro-Dateien
                    # normalerweise nur einige KB bis wenige MB gross.
                    moov_data = f.read(min(box_size, 16 * 1024 * 1024))
                    break
                pos += box_size

        if not moov_data:
            return None

        mpos = 8
        mn = len(moov_data)
        while mpos + 8 <= mn:
            b_size = struct.unpack(">I", moov_data[mpos:mpos + 4])[0]
            b_type = moov_data[mpos + 4:mpos + 8]
            if b_size < 8:
                break
            if b_type == b'mvhd':
                version = moov_data[mpos + 8]
                if version == 1:
                    creation_time = struct.unpack(">Q", moov_data[mpos + 12:mpos + 20])[0]
                else:
                    creation_time = struct.unpack(">I", moov_data[mpos + 12:mpos + 16])[0]
                if not creation_time:
                    return None
                # WICHTIG: Der mvhd-Zeitstempel ist laut MP4/QuickTime-
                # Spezifikation IMMER in UTC gespeichert, unabhaengig davon,
                # in welcher Zeitzone die GoPro tatsaechlich aufgenommen hat.
                # Frueher wurde dieser Wert direkt als Lokalzeit verwendet -
                # dadurch konnten z.B. abends/nachts aufgenommene Videos
                # (UTC ist in Deutschland 1-2h "in der Zukunft") beim
                # Zurueckrechnen auf den falschen, vorherigen Kalendertag
                # fallen und landeten so faelschlich im Datumsordner des
                # Vortags (oder sogar Vorvortags bei Aufnahmen kurz nach
                # Mitternacht). Fix: Zeitstempel explizit als UTC markieren
                # und dann in die lokale Zeitzone des Systems umrechnen,
                # BEVOR daraus der Kalendertag fuer den Zielordner bestimmt wird.
                if assume_local:
                    # HERO5 y anteriores: el valor ya es la hora LOCAL de
                    # la camara (no UTC); convertirlo correria la fecha.
                    dt = datetime(1904, 1, 1) + timedelta(seconds=creation_time)
                else:
                    dt_utc = datetime(1904, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=creation_time)
                    dt = dt_utc.astimezone().replace(tzinfo=None)
                # Grobe Plausibilitaetspruefung - manche Kameras/Schnitt-
                # programme schreiben hier unplausible Werte (0, Jahr 1904,
                # oder Datum in der Zukunft durch falsch gestellte Uhr).
                if dt.year < 1990 or dt > datetime.now() + timedelta(days=1):
                    return None
                return dt
            mpos += b_size
        return None
    except Exception:
        return None


def get_media_capture_date(path, mvhd_local=False):
    """Liefert das beste verfuegbare 'Aufnahmedatum' fuer eine Datei:
    zuerst aus den echten Metadaten (EXIF/MP4), sonst als Rueckfallebene
    das Datei-Aenderungsdatum auf der Platte."""
    ext = os.path.splitext(path)[1].lower()
    dt = None
    if ext in (".jpg", ".jpeg"):
        dt = _read_jpeg_exif_datetime(path)
    elif ext == ".mp4":
        dt = _read_mp4_creation_time(path, assume_local=mvhd_local)

    if dt is None:
        try:
            dt = datetime.fromtimestamp(os.path.getmtime(path))
        except Exception:
            dt = datetime.now()
    return dt


# --- [ VIDEOS DANADOS ] ---

def is_broken_mp4(path):
    """True si el MP4 no tiene la caja 'moov' (indice): video cortado por una
    copia interrumpida o una grabacion que no cerro bien. No se puede
    reproducir. Solo lee los encabezados de las cajas (unos bytes)."""
    if not path.lower().endswith(".mp4"):
        return False
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            pos = 0
            while pos + 8 <= size:
                f.seek(pos)
                h = f.read(8)
                n = struct.unpack(">I", h[:4])[0]
                if h[4:8] == b"moov":
                    return False
                if n == 1:
                    n = struct.unpack(">Q", f.read(8))[0]
                elif n == 0:
                    n = size - pos
                if n < 8:
                    break
                pos += n
        return True
    except Exception:
        return False


def replaceable_broken_copy(existing_path, new_size):
    """Un archivo ya existente con el mismo nombre que esta danado y es mas
    chico que el original: se reemplaza por la copia buena (no se crea _1)."""
    try:
        return os.path.getsize(existing_path) < new_size and is_broken_mp4(existing_path)
    except OSError:
        return False


# --- [ METADATOS DE ARCHIVOS (ficha de informacion) ] ---

_EXIF_TAGS = {0x010F: "make", 0x0110: "model", 0x0131: "software", 0x0132: "datetime", 0x9003: "taken",
              0x829A: "exposure", 0x829D: "fnumber", 0x8827: "iso", 0x920A: "focal", 0xA405: "focal35",
              0xA002: "width", 0xA003: "height"}


def read_jpeg_exif(path):
    """Lee los datos EXIF principales de un JPG (camara, fecha, exposicion,
    ISO, GPS...). Solo lee el principio del archivo."""
    out = {}
    try:
        with open(path, "rb") as f:
            head = f.read(256 * 1024)
        if head[:2] != b"\xff\xd8":
            return out
        pos = 2
        tiff = None
        while pos + 4 <= len(head) and head[pos] == 0xFF:
            marker, seg = head[pos + 1], struct.unpack(">H", head[pos + 2:pos + 4])[0]
            if marker == 0xE1 and head[pos + 4:pos + 10] == b"Exif\x00\x00":
                tiff = head[pos + 10:pos + 2 + seg]
                break
            if marker in (0xDA, 0xD9):
                break
            pos += 2 + seg
        if not tiff:
            return out
        bo = "<" if tiff[:2] == b"II" else ">"
        sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}

        def value(typ, count, raw_off):
            n = sizes.get(typ, 1) * count
            data = tiff[raw_off:raw_off + 4] if n <= 4 else tiff[struct.unpack(bo + "I", tiff[raw_off:raw_off + 4])[0]:][:n]
            if typ == 2:
                return data.split(b"\x00")[0].decode("latin1", "replace").strip()
            if typ == 3:
                return struct.unpack(bo + "H", data[:2])[0]
            if typ == 4:
                return struct.unpack(bo + "I", data[:4])[0]
            if typ in (5, 10):
                vals = []
                for i in range(count):
                    a, b = struct.unpack(bo + ("ii" if typ == 10 else "II"), data[i * 8:i * 8 + 8])
                    vals.append((a, b))
                return vals if count > 1 else vals[0]
            return None

        def read_ifd(off, into, tags=None, follow=True):
            n = struct.unpack(bo + "H", tiff[off:off + 2])[0]
            for i in range(n):
                e = off + 2 + 12 * i
                tag, typ, count = struct.unpack(bo + "HHI", tiff[e:e + 8])
                if follow and tag in (0x8769, 0x8825):
                    sub = struct.unpack(bo + "I", tiff[e + 8:e + 12])[0]
                    if tag == 0x8769:
                        read_ifd(sub, into)
                    else:
                        gps = {}
                        read_ifd(sub, gps, tags={1: "latref", 2: "lat", 3: "lonref", 4: "lon"}, follow=False)
                        into["gps"] = gps
                    continue
                key = (tags or _EXIF_TAGS).get(tag)
                if key:
                    try:
                        into[key] = value(typ, count, e + 8)
                    except Exception:
                        pass

        read_ifd(struct.unpack(bo + "I", tiff[4:8])[0], out)
    except Exception:
        pass
    return out


def _mp4_boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            break
        yield typ, pos + hdr, pos + size
        pos += size


# --- [ GPS DE LA GOPRO (telemetria GPMF en la pista "gpmd") ] ---

def _read_moov(path):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        fsize = f.tell()
        pos = 0
        while pos + 8 <= fsize:
            f.seek(pos)
            h = f.read(16)
            size = struct.unpack(">I", h[:4])[0]
            if size == 1:
                size = struct.unpack(">Q", h[8:16])[0]
            elif size == 0:
                size = fsize - pos
            if size < 8:
                return None
            if h[4:8] == b"moov":
                f.seek(pos)
                return f.read(min(size, 64 * 1024 * 1024))
            pos += size
    return None


def _child(data, start, end, typ):
    for t, a, b in _mp4_boxes(data, start, end):
        if t == typ:
            return a, b
    return None


def _gpmd_sample_ranges(moov):
    """(offset, tamano) de cada muestra de la pista de telemetria gpmd."""
    for t, a, b in _mp4_boxes(moov, 8):
        if t != b"trak":
            continue
        mdia = _child(moov, a, b, b"mdia")
        if not mdia:
            continue
        minf = _child(moov, *mdia, b"minf")
        stbl = _child(moov, *minf, b"stbl") if minf else None
        stsd = _child(moov, *stbl, b"stsd") if stbl else None
        if not stsd or moov[stsd[0] + 12:stsd[0] + 16] != b"gpmd":
            continue
        sz = _child(moov, *stbl, b"stsz")
        co = _child(moov, *stbl, b"stco")
        co64 = _child(moov, *stbl, b"co64")
        sc = _child(moov, *stbl, b"stsc")
        if not sz or not sc or not (co or co64):
            return []
        fixed, count = struct.unpack(">II", moov[sz[0] + 4:sz[0] + 12])
        sizes = [fixed] * count if fixed else list(struct.unpack(f">{count}I", moov[sz[0] + 12:sz[0] + 12 + 4 * count]))
        if co:
            n = struct.unpack(">I", moov[co[0] + 4:co[0] + 8])[0]
            offsets = list(struct.unpack(f">{n}I", moov[co[0] + 8:co[0] + 8 + 4 * n]))
        else:
            n = struct.unpack(">I", moov[co64[0] + 4:co64[0] + 8])[0]
            offsets = list(struct.unpack(f">{n}Q", moov[co64[0] + 8:co64[0] + 8 + 8 * n]))
        ne = struct.unpack(">I", moov[sc[0] + 4:sc[0] + 8])[0]
        entries = [struct.unpack(">III", moov[sc[0] + 8 + 12 * i:sc[0] + 20 + 12 * i]) for i in range(ne)]
        out, si = [], 0
        for ci, off in enumerate(offsets, start=1):
            per = 1
            for first, spc, _d in entries:
                if ci >= first:
                    per = spc
            for _ in range(per):
                if si >= len(sizes):
                    break
                out.append((off, sizes[si]))
                off += sizes[si]
                si += 1
        return out
    return []


_GPMF_FMT = {"b": "b", "B": "B", "s": "h", "S": "H", "l": "i", "L": "I", "f": "f", "d": "d", "j": "q", "J": "Q"}


def _gpmf_walk(buf, pos, end, out):
    """Recorre el KLV de GPMF y junta los puntos GPS5 (con su SCAL y la calidad)."""
    scal, fix, prec = None, 3, 0
    while pos + 8 <= end:
        key = buf[pos:pos + 4]
        typ = chr(buf[pos + 4])
        ssize = buf[pos + 5]
        rep_ = struct.unpack(">H", buf[pos + 6:pos + 8])[0]
        dstart = pos + 8
        dlen = ssize * rep_
        if dstart + dlen > end:
            break
        if buf[pos + 4] == 0:                       # contenedor (DEVC / STRM)
            _gpmf_walk(buf, dstart, dstart + dlen, out)
        elif key in (b"SCAL", b"GPSF", b"GPSP", b"GPS5") and typ in _GPMF_FMT:
            fmt = _GPMF_FMT[typ]
            width = struct.calcsize(fmt)
            n = dlen // width
            vals = struct.unpack(f">{n}{fmt}", buf[dstart:dstart + n * width])
            if key == b"SCAL":
                scal = vals
            elif key == b"GPSF":
                fix = vals[0]
            elif key == b"GPSP":
                prec = vals[0]
            elif key == b"GPS5" and n >= 5:
                sc = list(scal) if scal and len(scal) >= 5 else [1] * 5
                block = []
                for i in range(0, n - 4, 5):
                    lat, lon, alt, v2, _v3 = (vals[i + k] / (sc[k] or 1) for k in range(5))
                    if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon):
                        block.append((lat, lon, alt, v2))
                out.append((fix, prec, block))       # un bloque por segundo, con su calidad
        pos = dstart + ((dlen + 3) & ~3)


def read_gopro_gps(path):
    """Bloques (fix, precision, [(lat, lon, alt m, velocidad 2D m/s)]) del GPS de un video."""
    moov = _read_moov(path)
    if not moov:
        return []
    pts = []
    with open(path, "rb") as f:
        for off, size in _gpmd_sample_ranges(moov):
            if size <= 0 or size > 4 * 1024 * 1024:
                continue
            f.seek(off)
            buf = f.read(size)
            _gpmf_walk(buf, 0, len(buf), pts)
    return pts


def _haversine(a, b):
    import math
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371000 * 2 * math.asin(min(1.0, math.sqrt(h)))


def clean_gps(blocks, duration):
    """Filtra el ruido del GPS y calcula las estadisticas. Mientras busca
    senal la posicion deriva cientos de metros aunque la camara este quieta:
    se usan solo segundos con buena senal, sin puntos lejos de la mediana del
    segundo ni saltos imposibles para la velocidad medida. La distancia sale
    de la velocidad del GPS (Doppler, confiable) a lo largo del tiempo."""
    if not blocks:
        return None
    sec = (duration / len(blocks)) if duration else 1.0
    ok = lambda f, d: d == 0 or d < 500
    min_fix = 3 if any(f >= 3 and ok(f, d) and pts for f, d, pts in blocks) else 2
    pts_out, speeds, kept_med, gap = [], [], None, 0
    for f, d, pts in blocks:
        gap += 1
        if f < min_fix or not ok(f, d) or not pts:
            continue
        lats = sorted(p[0] for p in pts)
        lons = sorted(p[1] for p in pts)
        med = (lats[len(lats) // 2], lons[len(lons) // 2])
        pts = [p for p in pts if _haversine(p, med) <= 50]
        if not pts:
            continue
        v = sum(p[3] for p in pts) / len(pts)
        if kept_med is not None and _haversine(med, kept_med) > 25 + 3 * max(v, 1.0) * gap * sec:
            continue                       # salto imposible: deriva del GPS
        kept_med, gap = med, 0
        pts_out.extend(pts)
        speeds.append(v)
    if len(pts_out) < 2:
        return None
    dist = sum(speeds) * sec
    alts = sorted(p[2] for p in pts_out)
    return {"points": pts_out,
            "dist": dist,
            "vmax": max(speeds) * 3.6,
            "vavg": (dist / (len(speeds) * sec) * 3.6) if speeds else 0,
            "amin": alts[int(len(alts) * 0.05)],
            "amax": alts[min(len(alts) - 1, int(len(alts) * 0.95))]}


def read_gopro_track(path):
    return clean_gps(read_gopro_gps(path), read_mp4_info(path).get("duration") or 0)


def read_mp4_info(path):
    """Duracion, resolucion, fps, codec y datos de la GoPro (firmware) de un MP4."""
    info = {}
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            fsize = f.tell()
            pos = 0
            moov = None
            while pos + 8 <= fsize:
                f.seek(pos)
                h = f.read(16)
                size = struct.unpack(">I", h[:4])[0]
                if size == 1:
                    size = struct.unpack(">Q", h[8:16])[0]
                elif size == 0:
                    size = fsize - pos
                if size < 8:
                    break
                if h[4:8] == b"moov":
                    f.seek(pos)
                    moov = f.read(min(size, 32 * 1024 * 1024))
                    break
                pos += size
        if not moov:
            return info
        for typ, a, b in _mp4_boxes(moov, 8):
            if typ == b"mvhd":
                v = moov[a]
                if v == 1:
                    ts, dur = struct.unpack(">IQ", moov[a + 20:a + 32])
                else:
                    ts, dur = struct.unpack(">II", moov[a + 12:a + 20])
                if ts:
                    info["duration"] = dur / ts
            elif typ == b"udta":
                for t2, a2, b2 in _mp4_boxes(moov, a, b):
                    if t2 == b"FIRM":
                        info["firmware"] = moov[a2:b2].split(b"\x00")[0].decode("latin1", "replace").strip()
            elif typ == b"trak":
                kind = None
                track = {}
                for t2, a2, b2 in _mp4_boxes(moov, a, b):
                    if t2 == b"tkhd":
                        track["w"] = struct.unpack(">I", moov[b2 - 8:b2 - 4])[0] >> 16
                        track["h"] = struct.unpack(">I", moov[b2 - 4:b2])[0] >> 16
                    elif t2 == b"mdia":
                        for t3, a3, b3 in _mp4_boxes(moov, a2, b2):
                            if t3 == b"hdlr":
                                kind = moov[a3 + 8:a3 + 12]
                            elif t3 == b"mdhd":
                                v = moov[a3]
                                track["timescale"] = struct.unpack(">I", moov[a3 + (20 if v == 1 else 12):a3 + (24 if v == 1 else 16)])[0]
                            elif t3 == b"minf":
                                for t4, a4, b4 in _mp4_boxes(moov, a3, b3):
                                    if t4 == b"stbl":
                                        for t5, a5, b5 in _mp4_boxes(moov, a4, b4):
                                            if t5 == b"stsd":
                                                track["codec"] = moov[a5 + 12:a5 + 16].decode("latin1", "replace")
                                            elif t5 == b"stts" and struct.unpack(">I", moov[a5 + 4:a5 + 8])[0]:
                                                track["delta"] = struct.unpack(">I", moov[a5 + 12:a5 + 16])[0]
                if kind == b"vide" and "video" not in info:
                    info["video"] = track
                elif kind == b"soun" and "audio" not in info:
                    info["audio"] = track
    except Exception:
        pass
    return info


_CODEC_NAMES = {"avc1": "H.264 (AVC)", "hvc1": "H.265 (HEVC)", "hev1": "H.265 (HEVC)", "mp4a": "AAC"}


def describe_media(path, lang):
    """Lista de (seccion, etiqueta, valor) para la ficha de informacion."""
    rows = []
    try:
        st = os.stat(path)
        rows += [("file", tr(lang, "info_name"), os.path.basename(path)),
                 ("file", tr(lang, "info_path"), os.path.dirname(path)),
                 ("file", tr(lang, "info_size"), f"{_human_size(st.st_size)}  ({st.st_size:,} bytes)".replace(",", ".")),
                 ("file", tr(lang, "info_created"), datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_ctime)).strftime("%d/%m/%Y %H:%M:%S")),
                 ("file", tr(lang, "info_modified"), datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M:%S"))]
    except OSError:
        pass
    low = path.lower()
    media = []
    if low.endswith((".jpg", ".jpeg")):
        ex = read_jpeg_exif(path)
        cam = " ".join(x for x in (ex.get("make"), ex.get("model")) if x)
        if cam:
            media.append((tr(lang, "info_camera"), cam))
        if ex.get("software"):
            media.append((tr(lang, "info_firmware"), ex["software"]))
        taken = ex.get("taken") or ex.get("datetime")
        if taken:
            try:
                taken = datetime.strptime(taken, "%Y:%m:%d %H:%M:%S").strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                pass
            media.append((tr(lang, "info_taken"), taken))
        if ex.get("width") and ex.get("height"):
            mp = ex["width"] * ex["height"] / 1e6
            media.append((tr(lang, "info_resolution"), f"{ex['width']} × {ex['height']}  ({mp:.1f} MP)"))
        if isinstance(ex.get("exposure"), tuple) and ex["exposure"][1]:
            a, b = ex["exposure"]
            media.append((tr(lang, "info_exposure"), f"1/{round(b / a)} s" if a and a < b else f"{a / b:g} s"))
        if isinstance(ex.get("fnumber"), tuple) and ex["fnumber"][1]:
            media.append((tr(lang, "info_aperture"), f"f/{ex['fnumber'][0] / ex['fnumber'][1]:.1f}"))
        if ex.get("iso"):
            media.append((tr(lang, "info_iso"), str(ex["iso"])))
        if isinstance(ex.get("focal"), tuple) and ex["focal"][1]:
            txt = f"{ex['focal'][0] / ex['focal'][1]:.1f} mm"
            if ex.get("focal35"):
                txt += f"  ({ex['focal35']} mm eq. 35 mm)"
            media.append((tr(lang, "info_focal"), txt))
        gps = ex.get("gps") or {}
        try:
            def deg(v):
                return v[0][0] / v[0][1] + v[1][0] / v[1][1] / 60 + v[2][0] / v[2][1] / 3600
            lat = deg(gps["lat"]) * (-1 if gps.get("latref") == "S" else 1)
            lon = deg(gps["lon"]) * (-1 if gps.get("lonref") == "W" else 1)
            media.append((tr(lang, "info_gps"), f"{lat:.6f}, {lon:.6f}"))
        except Exception:
            pass
    elif low.endswith(".mp4"):
        if is_broken_mp4(path):
            media.append((tr(lang, "info_damaged"), tr(lang, "video_damaged")))
        else:
            mi = read_mp4_info(path)
            if mi.get("firmware"):
                media.append((tr(lang, "info_firmware"), mi["firmware"]))
            if mi.get("duration"):
                d = int(mi["duration"])
                media.append((tr(lang, "info_duration"), f"{d // 3600:d}:{d % 3600 // 60:02d}:{d % 60:02d}" if d >= 3600
                              else f"{d // 60:d}:{d % 60:02d}"))
            v = mi.get("video") or {}
            if v.get("w"):
                media.append((tr(lang, "info_resolution"), f"{v['w']} × {v['h']}"))
            if v.get("timescale") and v.get("delta"):
                media.append((tr(lang, "info_fps"), f"{v['timescale'] / v['delta']:.2f}".rstrip("0").rstrip(".")))
            if v.get("codec"):
                media.append((tr(lang, "info_codec"), _CODEC_NAMES.get(v["codec"], v["codec"])))
            a = mi.get("audio") or {}
            if a.get("codec"):
                rate = f", {a['timescale'] / 1000:g} kHz" if a.get("timescale") else ""
                media.append((tr(lang, "info_audio"), _CODEC_NAMES.get(a["codec"], a["codec"]) + rate))
    if not media:
        media.append(("", tr(lang, "info_none")))
    rows += [("media", k, v) for k, v in media]
    return rows
