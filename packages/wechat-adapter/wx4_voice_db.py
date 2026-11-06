"""WeChat 4.x received-voice reader: SQLCipher DB keys + SILK payload.

WeChat 4.x never drops received voice messages on disk as .silk files: the
SILK payload lives inside the encrypted media_0.db (table VoiceInfo, column
voice_data), so reading "ears" requires

1. recovering the per-database SQLCipher key from the live Weixin.exe process
   (WeChat 4.1.11+ keeps it XOR-masked inside the
   com.Tencent.WCDB.Config.Cipher object, not as plain x'<key><salt>'),
2. decrypting media_0.db (page 4096, reserve 80, AES-256-CBC) plus its -wal
   frames into a temp copy,
3. reading VoiceInfo rows and handing the SILK bytes on.

Everything here is filesystem / process-memory only: no window focus, no input
injection, no UIA. Algorithm credits: wechat-local-reader (wxkey.py) and
wechat-decrypt; write-up in docs/reports/voice-receive-silk-db.md.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import hmac as hmac_mod
import json
import os
import re
import sqlite3
import struct
import tempfile
import time

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80
IV_OFFSET = PAGE_SZ - RESERVE_SZ
SQLITE_HDR = b"SQLite format 3\x00"

CONFIG_NEEDLE = b"com.Tencent.WCDB.Config.Cipher"
CONFIG_MASK = bytes.fromhex(
    "d2c7442458020000004889442450488b"
    "450048844c2448488944254048584c24"
)
CONFIG_BLOB_MAX = 1024
CONFIG_LITERAL_RE = re.compile(rb"[xX]'([0-9a-fA-F]{64,192})'")

KEY_CACHE = os.path.join(
    os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "xfy-wx4-db-keys.json"
)

# 风控：扫 Weixin.exe 内存取 SQLCipher 密钥是敏感动作，正常情况下只在「冷启动 / 缓存失效」
# 时发生一次（缓存命中约 8ms，根本不碰内存）。这里再加一道硬闸：任何两次扫描至少隔
# SCAN_GUARD_S 秒，杜绝高频轮询扫描被微信风控盯上。
SCAN_GUARD_S = 600
_LAST_SCAN_AT = 0.0

MAX_ADDR = 0x0000_8000_0000_0000
CHUNK = 2 * 1024 * 1024
READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
TH32CS_SNAPPROCESS = 0x00000002


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("__alignment1", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("__alignment2", wt.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


def find_pids(image_name: str = "Weixin.exe") -> list:
    """All PIDs whose executable name matches (case-insensitive)."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (0, -1):
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    pids = []
    try:
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == image_name.lower():
                pids.append(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return pids


def _enumerate_regions(handle) -> list:
    regions = []
    addr = 0
    mbi = MBI()
    while addr < MAX_ADDR:
        got = kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr),
                                      ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not got:
            break
        base = int(mbi.BaseAddress or addr)
        size = int(mbi.RegionSize)
        if mbi.State == MEM_COMMIT and mbi.Protect in READABLE and size:
            regions.append((base, size))
        if size <= 0:
            break
        addr = base + size
    return regions


def _read_mem(handle, addr: int, size: int) -> bytes:
    buf = ctypes.create_string_buffer(size)
    n = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(n))
    if not ok or n.value == 0:
        return b""
    return buf.raw[: n.value]


def _u64(buf: bytes, off: int) -> int:
    if off < 0 or off + 8 > len(buf):
        return 0
    return struct.unpack_from("<Q", buf, off)[0]


def _xor_repeat(data: bytes, mask: bytes) -> bytes:
    return bytes(v ^ mask[i % len(mask)] for i, v in enumerate(data))


def _iter_chunks(regions, read, chunk: int = CHUNK, overlap: int = 0):
    for base, size in regions:
        off = 0
        tail = b""
        tail_base = base
        while off < size:
            take = min(chunk, size - off)
            data = read(base + off, take) or b""
            data_base = tail_base if tail else base + off
            merged = tail + data
            if merged:
                yield data_base, merged
            if overlap:
                tail = merged[-overlap:] if merged else b""
                tail_base = data_base + max(0, len(merged) - len(tail))
            else:
                tail = b""
                tail_base = base + off + take
            off += take


def mac_key_of(enc_key: bytes, salt: bytes) -> bytes:
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)


def verify_key(enc_key: bytes, page1: bytes) -> bool:
    """True when enc_key decrypts page1's HMAC (page1 = raw encrypted page)."""
    if len(page1) < PAGE_SZ:
        return False
    salt = page1[:SALT_SZ]
    hmac_data = page1[SALT_SZ: IV_OFFSET + IV_SZ]
    stored = page1[PAGE_SZ - HMAC_SZ:]
    h = hmac_mod.new(mac_key_of(enc_key, salt), hmac_data, hashlib.sha512)
    h.update(struct.pack("<I", 1))
    return hmac_mod.compare_digest(h.digest(), stored)


def collect_dbs(db_dir: str) -> list:
    dbs = []
    for root, _dirs, files in os.walk(db_dir):
        for name in files:
            if not name.endswith(".db"):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) < PAGE_SZ:
                    continue
                with open(path, "rb") as fh:
                    page1 = fh.read(PAGE_SZ)
            except OSError:
                continue
            dbs.append({
                "rel": os.path.relpath(path, db_dir).replace("\\", "/"),
                "path": path,
                "page1": page1,
                "salt": page1[:SALT_SZ].hex(),
            })
    dbs.sort(key=lambda d: d["rel"])
    return dbs


def config_cipher_scan(pid: int, dbs: list):
    """WeChat 4.1.11+: decode the XOR-masked x'<enc_key><salt>' cipher blobs."""
    handle = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    stats = {"needle": 0, "nodes": 0, "blobs": 0, "candidates": 0, "matched": 0}
    if not handle:
        stats["error"] = "open-process-failed"
        return {}, stats
    try:
        regions = _enumerate_regions(handle)

        def read(addr, size):
            return _read_mem(handle, addr, size)

        needle_addrs = set()
        for base, data in _iter_chunks(regions, read, overlap=len(CONFIG_NEEDLE) - 1):
            pos = data.find(CONFIG_NEEDLE)
            while pos >= 0:
                needle_addrs.add(base + pos)
                pos = data.find(CONFIG_NEEDLE, pos + 1)
        stats["needle"] = len(needle_addrs)
        if not needle_addrs:
            return {}, stats

        pairs = [struct.pack("<Q", a) + struct.pack("<Q", len(CONFIG_NEEDLE)) for a in needle_addrs]
        found = {}
        for base, data in _iter_chunks(regions, read, overlap=0x80):
            for pat in pairs:
                pos = data.find(pat)
                while pos >= 0:
                    qaddr = base + pos
                    node = read(qaddr - 0x10, 0x50)
                    if node and len(node) >= 0x40 and _u64(node, 0x10) in needle_addrs \
                            and _u64(node, 0x18) == len(CONFIG_NEEDLE):
                        stats["nodes"] += 1
                        cfg = _u64(node, 0x28)
                        if 0x10000 <= cfg < MAX_ADDR:
                            obj = read(cfg + 0x88, 0x28)
                            if obj and len(obj) >= 0x18:
                                dptr, dlen = _u64(obj, 0x8), _u64(obj, 0x10)
                                if 0 < dlen <= CONFIG_BLOB_MAX and 0x10000 <= dptr < MAX_ADDR:
                                    blob = read(dptr, int(dlen))
                                    if blob and len(blob) == dlen:
                                        stats["blobs"] += 1
                                        decoded = _xor_repeat(blob, CONFIG_MASK)
                                        for m in CONFIG_LITERAL_RE.finditer(decoded):
                                            run = m.group(1).decode("ascii").lower()
                                            stats["candidates"] += 1
                                            if len(run) >= 96:
                                                found[run[64:96]] = run[:64]
                    pos = data.find(pat, pos + 1)
        stats["matched"] = len(found)
        return found, stats
    finally:
        kernel32.CloseHandle(handle)


def _load_cache() -> dict:
    try:
        with open(KEY_CACHE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(KEY_CACHE), exist_ok=True)
        tmp = KEY_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(tmp, KEY_CACHE)
    except OSError:
        pass


def resolve_keys(db_dir: str, wanted: list) -> dict:
    """Return {rel: bytes(enc_key)} for the requested DBs, cached by salt."""
    dbs = {d["rel"]: d for d in collect_dbs(db_dir)}
    todo = [r for r in wanted if r in dbs]
    cache = _load_cache()
    keys = {}
    stale = False
    for rel in todo:
        d = dbs[rel]
        entry = cache.get(d["path"])
        if entry and entry.get("salt") == d["salt"]:
            ek = bytes.fromhex(entry["enc_key"])
            if verify_key(ek, d["page1"]):
                keys[rel] = ek
                continue
        stale = True
    if stale or len(keys) != len(todo):
        global _LAST_SCAN_AT
        if _LAST_SCAN_AT and time.time() - _LAST_SCAN_AT < SCAN_GUARD_S:
            # 闸门内：不扫内存，拿已缓存的部分先用（拿不到就本轮不读，下次再说）
            return keys
        _LAST_SCAN_AT = time.time()
        found = {}
        for pid in find_pids("Weixin.exe"):
            try:
                found, _stats = config_cipher_scan(pid, list(dbs.values()))
            except OSError:
                continue
            if found:
                break
        for rel in todo:
            d = dbs[rel]
            ek_hex = found.get(d["salt"])
            if not ek_hex:
                continue
            ek = bytes.fromhex(ek_hex)
            if verify_key(ek, d["page1"]):
                keys[rel] = ek
                cache[d["path"]] = {"salt": d["salt"], "enc_key": ek_hex}
        _save_cache(cache)
    return keys


def _decrypt_page(page: bytes, enc_key: bytes, first: bool):
    """Decrypt one page. Page1 carries the salt at [0:16]; others do not."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(page) < PAGE_SZ:
        page = page + b"\x00" * (PAGE_SZ - len(page))
    iv = page[IV_OFFSET: IV_OFFSET + IV_SZ]
    ct = page[SALT_SZ: IV_OFFSET] if first else page[: IV_OFFSET]
    dec = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    body = dec.update(ct) + dec.finalize()
    if first:
        # body is plaintext[16:4016] (the KDF salt replaces the SQLite magic),
        # so the page is rebuilt as magic + body + reserve.
        page_size = int.from_bytes(body[0:2], "big")
        if page_size not in (1024, 2048, 4096, 8192, 16384, 32768):
            return None
        return SQLITE_HDR + body + b"\x00" * RESERVE_SZ
    return body + b"\x00" * RESERVE_SZ


def _wal_pages(wal_path: str, enc_key: bytes):
    """Decode committed page images from a SQLCipher WAL file."""
    pages = {}
    try:
        with open(wal_path, "rb") as fh:
            blob = fh.read()
    except OSError:
        return pages, 0
    if len(blob) < 32 or blob[:4] not in (b"\x37\x7f\x06\x82", b"\x37\x7f\x06\x83"):
        return pages, 0
    salt1, salt2 = blob[16:20], blob[20:24]
    frames = []
    off = 32
    while off + 24 + PAGE_SZ <= len(blob):
        page_no, dbsize = struct.unpack_from(">II", blob, off)
        fsalt1, fsalt2 = blob[off + 8: off + 12], blob[off + 12: off + 16]
        frame = blob[off + 24: off + 24 + PAGE_SZ]
        if page_no and fsalt1 == salt1 and fsalt2 == salt2:
            frames.append((page_no, dbsize, frame))
        off += 24 + PAGE_SZ
    # only frames up to the last commit are durable
    last_commit = -1
    for i, (_no, dbsize, _f) in enumerate(frames):
        if dbsize:
            last_commit = i
    dbsize_out = 0
    for page_no, dbsize, frame in frames[: last_commit + 1]:
        plain = _decrypt_page(frame, enc_key, page_no == 1)
        if plain is None and page_no == 1:
            plain = _decrypt_page(frame, enc_key, False)
        if plain is not None:
            pages[page_no] = plain
        if dbsize:
            dbsize_out = dbsize
    return pages, dbsize_out


def decrypt_database(src: str, dst: str, enc_key: bytes, apply_wal: bool = True) -> int:
    """Decrypt a SQLCipher DB (+ WAL) into dst. Returns page count."""
    try:
        with open(src, "rb") as fh:
            data = fh.read()
    except OSError:
        return 0
    total = len(data) // PAGE_SZ
    pages = {}
    for i in range(total):
        off = i * PAGE_SZ
        plain = _decrypt_page(data[off: off + PAGE_SZ], enc_key, i == 0)
        if plain is None:
            raise ValueError("bad key: page1 has no SQLite header")
        pages[i + 1] = plain
    hdr_pages = int.from_bytes(pages[1][28:32], "big")
    if hdr_pages:
        pages = {no: pg for no, pg in pages.items() if no <= hdr_pages}
    if apply_wal and os.path.exists(src + "-wal"):
        wpages, dbsize = _wal_pages(src + "-wal", enc_key)
        pages.update(wpages)
        if dbsize:
            pages = {no: pg for no, pg in pages.items() if no <= dbsize}
            pg1 = bytearray(pages[1])
            pg1[28:32] = dbsize.to_bytes(4, "big")
            pages[1] = bytes(pg1)
    if not pages:
        return 0
    tmp = dst + ".part"
    with open(tmp, "wb") as fh:
        for no in sorted(pages):
            fh.write(pages[no])
    os.replace(tmp, dst)
    return max(pages)


def md5_chat_suffix(user_name: str) -> str:
    return hashlib.md5(user_name.encode("utf-8")).hexdigest()


class VoiceDBReader:
    """Read received (and sent) voice messages out of an account's DB storage."""

    MEDIA_REL = "message/media_0.db"
    MSG_REL = "message/message_0.db"

    def __init__(self, db_dir: str, tmp_dir=None):
        self.db_dir = os.path.abspath(db_dir)
        self.tmp_dir = tmp_dir or os.path.join(tempfile.gettempdir(), "xfy-wx4-voice")
        os.makedirs(self.tmp_dir, exist_ok=True)
        self._chat_map = {}
        self._chat_ids = {}
        # db_dir = ...\<wxid>\db_storage，用账号目录名判断「是不是我自己发的」
        self.wxid = os.path.basename(os.path.dirname(self.db_dir))
        self.self_ids = set()

    def media_path(self) -> str:
        return os.path.join(self.db_dir, self.MEDIA_REL.replace("/", os.sep))

    def msg_path(self) -> str:
        return os.path.join(self.db_dir, self.MSG_REL.replace("/", os.sep))

    def mtime_sig(self) -> tuple:
        out = []
        for p in (self.media_path(), self.media_path() + "-wal", self.msg_path()):
            try:
                out.append(os.path.getmtime(p))
            except OSError:
                out.append(0.0)
        return tuple(out)

    def _key(self, rel: str) -> bytes:
        keys = resolve_keys(self.db_dir, [rel])
        if rel not in keys:
            raise RuntimeError("no SQLCipher key for " + rel + " (is WeChat running?)")
        return keys[rel]

    def decrypted_media(self) -> str:
        dst = os.path.join(self.tmp_dir, "media_0.db")
        decrypt_database(self.media_path(), dst, self._key(self.MEDIA_REL))
        return dst

    def decrypted_msg(self) -> str:
        dst = os.path.join(self.tmp_dir, "message_0.db")
        decrypt_database(self.msg_path(), dst, self._key(self.MSG_REL))
        return dst

    def _load_chat_map(self, media_db: str, msg_db: str) -> None:
        names = {}
        candidates = set()
        for db, sql in ((media_db, "select rowid, user_name from Name2Id"),
                        (msg_db, "select user_name from Name2Id")):
            try:
                con = sqlite3.connect(db)
                try:
                    for row in con.execute(sql):
                        if len(row) == 2:
                            names[int(row[0])] = row[1]
                        candidates.add(row[-1])
                finally:
                    con.close()
            except sqlite3.Error:
                pass
        tables = []
        try:
            con = sqlite3.connect(msg_db)
            try:
                tables = [r[0] for r in con.execute(
                    "select name from sqlite_master where type='table' and name like 'Msg_%'")]
            finally:
                con.close()
        except sqlite3.Error:
            pass
        by_md5 = {md5_chat_suffix(c): c for c in candidates if isinstance(c, str)}
        self._chat_map = {}
        for tbl in tables:
            suffix = tbl[4:]
            if suffix in by_md5:
                self._chat_map[suffix] = by_md5[suffix]
        self._chat_ids = names
        # 账号目录名带 _39c4 这类十六进制后缀，Name2Id 里是裸 wxid：按「目录名 == 名字」或「目录名以 名字_ 开头」识别自己
        # media_0.db 与 message_0.db 的 Name2Id rowid 不一样，self 只能靠全量名字集合识别
        acc = self.wxid or ""
        self.self_ids = {u for u in candidates
                         if isinstance(u, str) and u and (acc == u or acc.startswith(u + "_"))}

    def voices(self, limit: int = 50) -> list:
        """Newest-first voice rows: chat, chat_id, local_id, ts, silk, size."""
        media_db = self.decrypted_media()
        msg_db = self.decrypted_msg()
        self._load_chat_map(media_db, msg_db)
        out = []
        con = sqlite3.connect(media_db)
        try:
            rows = con.execute(
                "select chat_name_id, create_time, local_id, svr_id, voice_data from VoiceInfo "
                "order by create_time desc limit ?", (limit,)).fetchall()
        finally:
            con.close()
        sender_cache = {}
        for chat_id, ts, local_id, _svr, blob in rows:
            if not blob:
                continue
            silk = blob[1:] if blob[:1] == b"\x02" else blob
            chat = self._chat_ids.get(int(chat_id), "chat_" + str(chat_id))
            if chat not in sender_cache:
                sender_cache[chat] = self._senders(msg_db, chat)
            sender, status = sender_cache[chat].get(int(local_id), ("", 0))
            from_me = bool(sender) and sender in self.self_ids
            if not sender:
                from_me = int(status) == 2
            out.append({
                "chat_id": int(chat_id),
                "chat": chat,
                "local_id": int(local_id),
                "ts": float(ts),
                "silk": silk,
                "size": len(silk),
                "sender": sender or "",
                "status": int(status),
                "from_me": bool(from_me),
            })
        return out

    def _senders(self, msg_db: str, chat: str) -> dict:
        """local_id -> (发送者 wxid, status)，status=2 表示自己发出去的。"""
        tbl = "Msg_" + md5_chat_suffix(chat)
        out = {}
        try:
            con = sqlite3.connect(msg_db)
            try:
                rows = con.execute(
                    "select m.local_id, n.user_name, m.status from " + tbl + " m "
                    "left join Name2Id n on n.rowid = m.real_sender_id").fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return out
        for local_id, name, status in rows:
            out[int(local_id)] = (name or "", int(status or 0))
        return out

    def write_silk(self, rec: dict) -> str:
        path = os.path.join(self.tmp_dir, str(int(rec["ts"])) + "_" + str(rec["local_id"]) + ".silk")
        with open(path, "wb") as fh:
            fh.write(rec["silk"])
        return path
