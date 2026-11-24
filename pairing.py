"""Small, Qt-independent helpers for user-initiated DIRIGERA token pairing."""
from __future__ import annotations

import ipaddress
import os
import re
import sys
from contextlib import ExitStack, redirect_stdout

_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_TOKEN = re.compile(r"\bYour\s+token\s*:\s*([A-Za-z0-9._~-]{32,})", re.IGNORECASE)


def valid_hub_address(value: str) -> bool:
    """Accept syntactically valid IP literals or LAN hostnames, not URLs/ports.

    Validation here is intentionally offline: a syntactically valid address might
    still be unreachable, and that failure is reported when pairing is attempted.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if len(value) > 253 or any(char.isspace() for char in value):
        return False
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        # Reject malformed IPv4-looking text rather than treating it as a DNS name.
        if re.fullmatch(r"[0-9.]+", value):
            return False
        return all(_HOST_LABEL.fullmatch(part) for part in value.split("."))
    else:
        return not (addr.is_multicast or addr.is_unspecified or addr.is_loopback)


def extract_generated_token(output: str) -> str | None:
    """Find the generated token without ever printing it or logging the output."""
    if not isinstance(output, str):
        return None
    match = _TOKEN.search(output)
    return match.group(1) if match else None


def run_token_helper(ip: str) -> None:
    """Run the upstream CLI only when the app is launched as a helper process."""
    if not valid_hub_address(ip):
        raise SystemExit("Invalid DIRIGERA hub address")
    from dirigera.hub.auth import main
    sys.argv = ["generate-token", ip]
    if sys.stdin is not None and sys.stdout is not None:
        main()
        return

    # Windowed Windows builds have no Python console streams. QProcess
    # still supplies private pipe handles to its helper through CreateProcess.
    if sys.platform != "win32":
        raise SystemExit("Pairing helper requires input and output pipes")
    import ctypes
    from ctypes import wintypes
    import msvcrt

    get_std_handle = ctypes.WinDLL("kernel32", use_last_error=True).GetStdHandle
    get_std_handle.argtypes = (wintypes.DWORD,)
    get_std_handle.restype = wintypes.HANDLE
    invalid_handle = ctypes.c_void_p(-1).value
    with ExitStack() as stack:
        streams = []
        for std_handle, flags, mode in (
            (-10, os.O_RDONLY, "r"),
            (-11, os.O_WRONLY, "w"),
        ):
            handle = get_std_handle(std_handle)
            if handle in (None, invalid_handle):
                raise SystemExit("Pairing helper requires input and output pipes")
            fd = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
            streams.append(stack.enter_context(os.fdopen(fd, mode, encoding="utf-8", buffering=1)))
        stdin, stdout = streams
        original_stdin = sys.stdin
        try:
            sys.stdin = stdin
            with redirect_stdout(stdout):
                main()
        finally:
            sys.stdin = original_stdin
