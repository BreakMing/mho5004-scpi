"""Bundled VXI-11 text query/setting transport with original response evidence."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import json
import secrets
import socket
import struct
import time
import tomllib
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
CONFIG = tomllib.loads((ROOT / "config/instrument.toml").read_text(encoding="utf-8"))
RUN = None  # An owning task must select its unique evidence directory.


def require_host(value):
    """Reject an unset live target before any socket or DNS operation."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Instrument host is required; pass the confirmed address via --host or Runtime(host=...)')
    value = value.strip()
    if any(c.isspace() for c in value) or any(c in value for c in '/\\;'):
        raise ValueError('Instrument host must be an address or hostname, without a URL, path or whitespace')
    return value


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def save_event(kind, **fields):
    if RUN is None:
        raise RuntimeError("Select a unique evidence directory before opening a session")
    row = {"timestamp": now(), "event": kind, **fields}
    with (RUN / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + chr(10))


def recv_exact(sock, count):
    parts = []
    while count:
        part = sock.recv(count)
        if not part:
            raise ConnectionError("RPC peer closed mid-record")
        parts.append(part)
        count -= len(part)
    return b"".join(parts)


def xdr_bytes(value):
    return struct.pack("!I", len(value)) + value + bytes((-len(value)) % 4)


class Session:
    def __init__(self):
        self.host = require_host(CONFIG["connection"]["host"])
        self.portmapper = CONFIG["connection"]["portmapper_port"]
        self.device = CONFIG["vxi11"]["device_name"].encode("ascii")
        self.id = uuid4().hex
        self.sock = None
        self.link = None
        self.max_recv = 8192
        self.packets = []
        self.core_port = None

    def __enter__(self):
        if RUN is None:
            raise RuntimeError("An owning task must select its evidence directory before connection")
        with socket.create_connection((self.host, self.portmapper), timeout=3.0) as probe:
            probe.settimeout(3.0)
            body = self.rpc(probe, 100000, 2, 3, struct.pack("!IIII", 395183, 1, 6, 0))
            if len(body) != 4:
                raise ValueError("Portmapper returned invalid GETPORT body")
            self.core_port = struct.unpack("!I", body)[0]
            if not self.core_port:
                raise ConnectionError("VXI-11 core service not registered")
        self.sock = socket.create_connection((self.host, self.core_port), timeout=3.0)
        self.sock.settimeout(3.0)
        body = self.rpc(self.sock, 395183, 1, 10,
                        struct.pack("!III", secrets.randbits(32), 0, 3000) + xdr_bytes(self.device))
        if len(body) < 16:
            raise ValueError("Short CREATE_LINK response")
        error, self.link, abort_port, self.max_recv = struct.unpack("!IIII", body[:16])
        if error:
            raise RuntimeError("CREATE_LINK error " + str(error))
        self.max_recv = min(max(self.max_recv, 256), 8192)
        save_event("vxi11_connected", connection_id=self.id, host=self.host,
                   core_port=self.core_port, device_name=self.device.decode("ascii"),
                   max_recv=self.max_recv)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.sock and self.link is not None:
                self.rpc(self.sock, 395183, 1, 23, struct.pack("!I", self.link))
        except Exception as cleanup:
            save_event("vxi11_cleanup_problem", connection_id=self.id,
                       error_type=type(cleanup).__name__, message=str(cleanup))
        finally:
            if self.sock:
                self.sock.close()
            save_event("vxi11_closed", connection_id=self.id,
                       cause=type(exc).__name__ if exc else "normal")

    def rpc(self, sock, program, version, procedure, args, *,
            reply_limit=1048576, capture_path=None):
        # The default path is unchanged. A waveform-only caller may explicitly
        # request a larger bounded RPC record and save its received bytes,
        # including the prefix of a failed/incomplete record, to disk.
        if not 1 <= reply_limit <= 64 * 1024 * 1024:
            raise ValueError("RPC reply limit outside the 64 MiB bound")
        xid = secrets.randbits(32)
        call = struct.pack("!IIIIII", xid, 0, 2, program, version, procedure)
        call += struct.pack("!IIII", 0, 0, 0, 0) + args
        request = struct.pack("!I", 0x80000000 | len(call)) + call
        start = time.monotonic()
        sock.sendall(request)
        fragments = []
        fragment_lengths = []
        reply_parts = []
        total_length = 0
        captured = capture_path.open("xb") if capture_path is not None else None
        captured_hash = sha256()
        captured_bytes = 0
        complete_record = False

        def receive(count):
            nonlocal captured_bytes
            if captured is None:
                return recv_exact(sock, count)
            parts = []
            while count:
                part = sock.recv(min(count, 65536))
                if not part:
                    raise ConnectionError("RPC peer closed mid-record")
                captured.write(part)
                captured_hash.update(part)
                captured_bytes += len(part)
                parts.append(part)
                count -= len(part)
            return b"".join(parts)

        try:
            while True:
                head = receive(4)
                marker = struct.unpack("!I", head)[0]
                length = marker & 0x7FFFFFFF
                total_length += length
                if length > reply_limit or total_length > reply_limit:
                    raise ValueError("RPC record exceeds configured reply limit")
                part = receive(length)
                fragment_lengths.append(length)
                if captured is None:
                    fragments.append(head + part)
                reply_parts.append(part)
                if marker & 0x80000000:
                    complete_record = True
                    break
        finally:
            if captured is not None:
                captured.close()
                self.packets.append({"program": program, "procedure": procedure,
                    "request_hex": request.hex(),
                    "response_evidence": str(capture_path.relative_to(RUN)),
                    "response_bytes": captured_bytes,
                    "response_sha256": captured_hash.hexdigest(),
                    "rpc_fragment_lengths": fragment_lengths,
                    "declared_body_bytes": total_length,
                    "complete_rpc_record": complete_record,
                    "reply_limit": reply_limit,
                    "elapsed_s": time.monotonic() - start})
        if captured is None:
            reply_packet = b"".join(fragments)
            self.packets.append({"program": program, "procedure": procedure,
                                 "request_hex": request.hex(), "response_hex": reply_packet.hex(),
                                 "rpc_fragment_lengths": fragment_lengths,
                                 "elapsed_s": time.monotonic() - start})
        reply = b"".join(reply_parts)
        if len(reply) < 24:
            raise ValueError("Short RPC reply")
        rid, kind, status, verify_flavor, verify_len = struct.unpack("!IIIII", reply[:20])
        if rid != xid or kind != 1 or status != 0:
            raise ValueError("RPC reply mismatch or rejection")
        offset = 20 + (verify_len + 3) // 4 * 4
        if len(reply) < offset + 4:
            raise ValueError("Missing RPC acceptance status")
        accepted = struct.unpack("!I", reply[offset:offset + 4])[0]
        if accepted:
            raise ValueError("RPC call not accepted: " + str(accepted))
        return reply[offset + 4:]

    def query(self, case_id, command, expected_values=None, limit=None):
        suffix = command.split("?", 1)
        if ";" in command or len(suffix) != 2 or (suffix[1] and not suffix[1].startswith(" ")):
            raise ValueError("Expected one documented query with optional arguments, no semicolon")
        limit = limit or CONFIG["connection"]["max_text_bytes"]
        destination = RUN / "evidence" / case_id / uuid4().hex[:8]
        destination.mkdir(parents=True, exist_ok=False)
        raw_command = command.encode("ascii") + bytes([10])
        record = {"case_id": case_id, "command": command, "sent_hex": raw_command.hex(),
                  "connection_id": self.id, "status": "started", "expected_values": expected_values}
        started = time.monotonic()
        packet_start = len(self.packets)
        save_event("query_started", case_id=case_id, connection_id=self.id,
                   command=command, sent_hex=raw_command.hex())
        data = bytearray()
        try:
            write = self.rpc(self.sock, 395183, 1, 11,
                             struct.pack("!IIII", self.link, 3000, 3000, 8) + xdr_bytes(raw_command))
            if len(write) < 8:
                raise ValueError("Short DEVICE_WRITE result")
            write_error, count = struct.unpack("!II", write[:8])
            record["write_error"] = write_error
            record["write_count"] = count
            if write_error or count != len(raw_command):
                raise RuntimeError("Incomplete VXI-11 write")
            while len(data) < limit:
                body = self.rpc(self.sock, 395183, 1, 12,
                                struct.pack("!IIIIII", self.link, min(self.max_recv, limit - len(data)),
                                            3000, 3000, 0, 0))
                if len(body) < 12:
                    raise ValueError("Short DEVICE_READ result")
                error, reason, length = struct.unpack("!III", body[:12])
                if length > self.max_recv or len(body) < 12 + length:
                    raise ValueError("Invalid DEVICE_READ length")
                data.extend(body[12:12 + length])
                record["read_error"] = error
                record["read_reason"] = reason
                if error:
                    raise RuntimeError("DEVICE_READ error " + str(error))
                if reason & 4 or reason & 2:
                    break
                if not length:
                    raise ValueError("No progress before response end")
            else:
                raise ValueError("Text response hit configured byte limit")
            text = bytes(data).decode("ascii", errors="strict")
            record["response_text"] = text
            normalized = text.rstrip(chr(13) + chr(10))
            record["normalized"] = normalized
            record["receive_terminator_hex"] = "0d0a" if text.endswith(chr(13) + chr(10)) else "0a" if text.endswith(chr(10)) else ""
            record["expected_match"] = normalized in expected_values if expected_values else None
            record["status"] = "response_complete"
        except Exception as error:
            record["status"] = "communication_exception"
            record["error"] = {"type": type(error).__name__, "message": str(error)}
        finally:
            raw = bytes(data)
            raw_path = destination / "response.bin"
            raw_path.write_bytes(raw)
            record["response_bytes"] = len(raw)
            record["response_hex"] = raw.hex()
            record["response_sha256"] = sha256(raw).hexdigest()
            record["elapsed_s"] = time.monotonic() - started
            record["raw_evidence"] = str(raw_path.relative_to(RUN))
            packets_path = destination / "rpc-packets.json"
            packets_path.write_text(json.dumps(self.packets[packet_start:], ensure_ascii=False, indent=2), encoding="utf-8")
            record["rpc_evidence"] = str(packets_path.relative_to(RUN))
            result_path = destination / "result.json"
            result_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            save_event("query_finished", case_id=case_id, connection_id=self.id,
                       result_path=str(result_path.relative_to(RUN)), status=record["status"],
                       response_sha256=record["response_sha256"])
        return record

    def write_command(self, case_id, command):
        """Send one documented setting/event command; do not assume silence means accepted."""
        if ";" in command or command.endswith("?") or not command.startswith(":"):
            raise ValueError("Expected one documented SCPI setting/event command")
        destination = RUN / "evidence" / case_id / uuid4().hex[:8]
        destination.mkdir(parents=True, exist_ok=False)
        raw_command = command.encode("ascii") + b"\n"
        record = {"case_id": case_id, "command": command, "sent_hex": raw_command.hex(),
                  "connection_id": self.id, "status": "started"}
        packet_start = len(self.packets)
        started = time.monotonic()
        save_event("setting_started", case_id=case_id, connection_id=self.id,
                   command=command, sent_hex=raw_command.hex())
        try:
            write = self.rpc(self.sock, 395183, 1, 11,
                             struct.pack("!IIII", self.link, 3000, 3000, 8) + xdr_bytes(raw_command))
            if len(write) < 8:
                raise ValueError("Short DEVICE_WRITE result")
            error, count = struct.unpack("!II", write[:8])
            record.update(write_error=error, write_count=count)
            if error or count != len(raw_command):
                raise RuntimeError("Incomplete VXI-11 write")
            record["status"] = "write_complete_unconfirmed"
        except Exception as error:
            record.update(status="communication_exception",
                          error={"type": type(error).__name__, "message": str(error)})
        finally:
            record["elapsed_s"] = time.monotonic() - started
            packets_path = destination / "rpc-packets.json"
            packets_path.write_text(json.dumps(self.packets[packet_start:], ensure_ascii=False, indent=2), encoding="utf-8")
            record["rpc_evidence"] = str(packets_path.relative_to(RUN))
            result_path = destination / "result.json"
            result_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            save_event("setting_sent", case_id=case_id, connection_id=self.id,
                       command=command, status=record["status"],
                       result_path=str(result_path.relative_to(RUN)))
        return record

