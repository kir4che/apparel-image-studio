"""Isolated browser checks; never delete pairs from the user's live session."""
from http.server import HTTPServer
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading

from PIL import Image
from garment_pilot import ROOT
from studio_core import StudioStore
from studio_server import Handler

SAMPLE = Path("/Users/moinmoin/Downloads/原圖")


def main():
    run = Path(tempfile.mkdtemp(prefix="studio-changes-", dir=ROOT))
    store = StudioStore(run / "data", run / "exports")
    photos = [store.import_photo(f"下載 ({i}).jpeg", (SAMPLE / f"下載 ({i}).jpeg").read_bytes())["photo"] for i in (1, 2, 3, 4)]
    def side(i):
        return {"photo": photos[i]["id"], "top": 0, "cut": False}
    store.update({"product": "刪除配對測試", "format": "natural", "groups": [{"id": "first", "left": side(0), "right": side(1)}, {"id": "second", "left": side(2), "right": side(3)}]})
    fixtures = run / "formats"
    fixtures.mkdir()
    for i, (ext, fmt) in enumerate([("JPEG", "JPEG"), ("webp", "WEBP"), ("avif", "AVIF"), ("bmp", "BMP"), ("tiff", "TIFF"), ("gif", "GIF")]):
        Image.new("RGB", (180, 260), (30 + i * 20, 60, 90)).save(fixtures / f"格式測試.{ext}", fmt)
    (fixtures / "損壞.webp").write_bytes(b"not a photo")
    Image.new("RGB", (20, 30), "red").save(fixtures / "動畫.webp", "WEBP", save_all=True, append_images=[Image.new("RGB", (20, 30), "blue")])
    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.store, server.token, server.exports = store, secrets.token_urlsafe(32), set()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {**os.environ, "PAIR_TEST_URL": f"http://127.0.0.1:{server.server_port}", "PAIR_TEST_RUN": str(run)}
    try:
        subprocess.run(["/Users/moinmoin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node", str(ROOT / "test_studio_changes.cjs")], env=env, check=True)
        print(f"BROWSER_CHECKS: {run}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
