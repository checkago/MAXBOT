import os
import ssl
from pathlib import Path


_CONTEXT: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    global _CONTEXT
    if _CONTEXT is not None:
        return _CONTEXT

    ctx = ssl.create_default_context()
    extra_dirs = [
        os.getenv("EXTRA_CA_DIR", "/usr/local/share/ca-certificates"),
        "certs",
        ".",
    ]
    loaded = 0
    for directory in extra_dirs:
        path = Path(directory)
        if not path.is_dir():
            continue
        for cert in (*path.glob("*.crt"), *path.glob("*.cer"), *path.glob("*.pem")):
            try:
                ctx.load_verify_locations(cafile=str(cert))
                loaded += 1
            except ssl.SSLError:
                continue
    if loaded:
        print(f"SSL extra CA files loaded: {loaded}")
    _CONTEXT = ctx
    return ctx
