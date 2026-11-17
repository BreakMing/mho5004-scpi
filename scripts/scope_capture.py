"""Retrieve one newly created scope screenshot; ignore transient FTP .pending names.

Use the post-command directory difference rather than a fixed calendar date.
"""
from ftplib import FTP
from hashlib import sha256
from pathlib import Path
from PIL import Image
import time

from scope_transport import RUN

def capture_one(ftp: FTP, session, case_id: str, name_prefix: str = "Screenshot_") -> dict:
    before = set(ftp.nlst("/Pictures/Screenshots"))
    sent = session.write_command(case_id + ".CAPTURE", ":STORage:CAPTure:STARt")
    if sent["status"] != "write_complete_unconfirmed":
        raise RuntimeError("capture write uncertain; do not repeat blindly")
    deadline = time.monotonic() + 15
    candidates = []
    pending = []
    while time.monotonic() < deadline:
        diff = set(ftp.nlst("/Pictures/Screenshots")) - before
        pending.extend(sorted(x for x in diff if x.startswith(".pending-")))
        candidates = sorted(x for x in diff if x.startswith(name_prefix) and x.endswith(".png"))
        if candidates:
            break
        time.sleep(.35)
    if len(candidates) != 1:
        raise RuntimeError("expected one new completed PNG, got " + repr(candidates))
    name = candidates[0]
    local = RUN / "device-files" / (case_id + "-" + name)
    if local.exists():
        raise RuntimeError("local screenshot path already exists")
    with local.open("wb") as stream:
        transfer = ftp.retrbinary("RETR /Pictures/Screenshots/" + name, stream.write, blocksize=65536)
    data = local.read_bytes()
    with Image.open(local) as im:
        im.verify()
    with Image.open(local) as im:
        info = {"format": im.format, "size": list(im.size), "mode": im.mode}
    if info != {"format": "PNG", "size": [1920, 1200], "mode": "RGB"}:
        raise RuntimeError("unexpected screenshot format " + repr(info))
    return {"capture_evidence": sent["rpc_evidence"],
            "ftp_path": "/Pictures/Screenshots/" + name,
            "file_evidence": str(local.relative_to(RUN)),
            "file_bytes": len(data), "file_sha256": sha256(data).hexdigest(),
            "image": info, "pending_seen": list(dict.fromkeys(pending)),
            "ftp_transfer": transfer}
