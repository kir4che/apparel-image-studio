"""Loopback-only desktop studio UI. No external network or shell execution from input."""
import argparse
import base64
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from urllib.parse import urlsplit, parse_qs, unquote
import webbrowser
import time
from collections import OrderedDict
from PIL import Image

from garment_pilot import MODEL, ROOT, analyze, detect_engine, digest, get_engine, prune_cache, read_image, set_engine, top_only_crop_box
from studio_core import StudioStore

if getattr(sys, 'frozen', False):
    WEB = Path(getattr(sys, '_MEIPASS', ROOT)) / "studio-web"
else:
    WEB = ROOT / "studio-web"

AI_LOG_DIR = ROOT / "logs"
AI_LOG_LOCK = threading.Lock()


def _ai_attempt_summary(attempt):
    raw = attempt.get("raw_response")
    if raw is None:
        excerpt = None
    elif isinstance(raw, str):
        excerpt = raw[:2000]
    else:
        excerpt = json.dumps(raw, ensure_ascii=False, default=str)[:2000]
    return {
        "engine": attempt.get("engine"),
        "seconds": attempt.get("seconds"),
        "error": attempt.get("error"),
        "rejected_issues": attempt.get("rejected_issues", []),
        "response_excerpt": excerpt,
    }


def write_ai_log(run_id, event, log_dir=None):
    """Append one bounded diagnostic event without recording source image data."""
    folder = Path(log_dir or AI_LOG_DIR)
    suffix = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
    path = folder / f"ai-{time.strftime('%Y%m%d')}-{suffix}.jsonl"
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "run_id": run_id,
        **event,
    }
    try:
        folder.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        with AI_LOG_LOCK:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
    except OSError as exc:
        print(f"AI 診斷紀錄寫入失敗：{exc}", flush=True)
        return None
    return str(path)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(600)

    def log_message(self, *args):
        pass

    def send(self, body, content_type="application/json", status=200):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def allowed(self, write=False):
        host = self.headers.get("Host", "")
        port = self.server.server_port
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.send({"error": "只允許從本機工具存取"}, status=403)
            return False
        if write and (self.headers.get("Origin") not in (None, f"http://127.0.0.1:{port}", f"http://localhost:{port}") or not secrets.compare_digest(self.headers.get("X-Pairing-Token", ""), self.server.token)):
            self.send({"error": "連線驗證失效，請重新整理頁面"}, status=403)
            return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        route = urlsplit(self.path)
        store = self.server.store
        try:
            if route.path == "/api/health":
                self.send({"app": "local-clothing-pairs-v1"})
            elif route.path == "/api/ai-health":
                engine, detail = detect_engine()
                self.send({"ok": engine is not None, "engine": engine, "detail": detail})
            elif route.path == "/api/ai-engine":
                engine = get_engine()
                self.send({"engine": engine})
            elif route.path == "/api/state":
                self.send({"token": self.server.token, **store.state})
            elif route.path in ("/", "/app.js", "/style.css", "/ai.css"):
                path = WEB / {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/ai.css": "ai.css"}[route.path]
                mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}[path.suffix]
                self.send(path.read_bytes(), mime)
            elif route.path.startswith("/api/photo/"):
                photo = store.photo(route.path.split("/")[-1])
                view = parse_qs(route.query).get("view", [""])[0]
                if view == "crop" and store.state.get("photo_crops", {}).get(photo["id"]):
                    image = read_image(store.folder / f'{photo["id"]}.png', formats=("PNG",))
                    image = image.crop(store.state["photo_crops"][photo["id"]])
                    image.thumbnail((800, 800))
                    buf = io.BytesIO(); image.save(buf, "JPEG", quality=90)
                    self.send(buf.getvalue(), "image/jpeg")
                elif view == "ai" and store.state.get("ai_crops", {}).get(photo["id"]):
                    image = read_image(store.folder / f'{photo["id"]}.png', formats=("PNG",))
                    top = store.state["ai_crops"][photo["id"]]
                    image = image.crop((photo["white"][0], top, photo["white"][1], photo["height"]))
                    image.thumbnail((800, 800))
                    buf = io.BytesIO(); image.save(buf, "JPEG", quality=90)
                    self.send(buf.getvalue(), "image/jpeg")
                else:
                    self.send((store.folder / f'{photo["id"]}-thumb.jpg').read_bytes(), "image/jpeg")
            else:
                self.send({"error": "找不到頁面"}, status=404)
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            error = "照片像素過高，請使用較小的照片或先轉存一份工作檔" if isinstance(exc, (Image.DecompressionBombError, Image.DecompressionBombWarning)) else str(exc)
            self.send({"error": error}, status=400)

    def do_POST(self):
        if not self.allowed(write=True):
            return
        route = urlsplit(self.path)
        store = self.server.store
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = 40_000_000 if route.path == "/api/import" else 200_000
            if not 0 < length <= limit:
                raise ValueError("資料過大或沒有內容")
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("傳輸中斷，請重新匯入")
            if route.path == "/api/import":
                name = parse_qs(route.query).get("name", [""])[0]
                self.send(store.import_photo(name, body))
                return
            data = json.loads(body)
            if route.path == "/api/state":
                store.update(data)
                self.send({"saved": True})
            elif route.path == "/api/ai-engine":
                engine = data.get("engine")
                if engine not in ("auto", "llamacpp", "lmstudio"):
                    raise ValueError("無效的 AI 引擎，請選擇 auto、llamacpp 或 lmstudio")
                set_engine(engine)
                self.send({"engine": engine})
            elif route.path == "/api/photo/delete":
                if not isinstance(data, dict) or not isinstance(data.get("photo_id"), str):
                    raise ValueError("照片資料格式無效")
                deleted = store.delete_photo(data["photo_id"])
                self.send({"deleted": True, "id": deleted["id"], "name": deleted["name"]})
            elif route.path == "/api/photos/delete":
                if not isinstance(data, dict):
                    raise ValueError("照片清單格式無效")
                deleted = store.delete_photos(data.get("photo_ids"))
                self.send({"deleted": True, "count": len(deleted), "ids": [photo["id"] for photo in deleted]})
            elif route.path == "/api/preview":
                image, info = store.compose_preview(data["group"], data["format"])
                buf = io.BytesIO()
                image.save(buf, "JPEG", quality=86, optimize=True)
                self.send({"image": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), **info})
            elif route.path == "/api/photo-preview":
                if not isinstance(data, dict) or not isinstance(data.get("photo_id"), str):
                    raise ValueError("照片資料格式無效")
                image, info = store.photo_preview({"photo": data["photo_id"], "box": data.get("box")})
                buf = io.BytesIO()
                image.save(buf, "JPEG", quality=86, optimize=True)
                self.send({"image": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), "size": list(image.size), **info})
            elif route.path == "/api/ai-cancel":
                run_id = data.get("run_id") if isinstance(data, dict) else None
                if not isinstance(run_id, str) or not run_id or len(run_id) > 100:
                    raise ValueError("AI 分析批次格式無效")
                cancelled = getattr(self.server, "ai_cancelled", OrderedDict())
                now = time.monotonic()
                cancelled[run_id] = now
                expired = [k for k, t in cancelled.items() if now - t > 600]
                for k in expired:
                    del cancelled[k]
                while len(cancelled) > 200:
                    cancelled.popitem(last=False)
                self.server.ai_cancelled = cancelled
                log_file = write_ai_log(run_id, {"event": "cancel_requested"})
                self.send({"cancelled": True, "run_id": run_id, "log_file": log_file})
            elif route.path == "/api/ai-crop":
                if not isinstance(data, dict):
                    raise ValueError("AI 分析資料格式無效")
                ids = data.get("photo_ids")
                if ids is not None and (not isinstance(ids, list) or any(not isinstance(x, str) for x in ids)):
                    raise ValueError("照片清單格式無效")
                run_id = data.get("run_id")
                if not isinstance(run_id, str) or not run_id or len(run_id) > 100:
                    raise ValueError("AI 分析批次格式無效")
                batch_index = data.get("batch_index")
                batch_total = data.get("batch_total")
                if batch_index is not None and (not isinstance(batch_index, int) or batch_index < 1):
                    raise ValueError("AI 分析進度格式無效")
                if batch_total is not None and (not isinstance(batch_total, int) or batch_total < 1):
                    raise ValueError("AI 分析進度格式無效")
                if batch_index is not None and batch_total is not None and batch_index > batch_total:
                    raise ValueError("AI 分析進度格式無效")
                if run_id in getattr(self.server, "ai_cancelled", set()):
                    self.send({"cancelled": True, "run_id": run_id, "results": []})
                    return
                photos = store.state["photos"] if ids is None else [store.photo(x) for x in ids]
                if len(photos) > 100:
                    raise ValueError("一次最多分析 100 張照片")
                prune_cache(ROOT / "cache")
                results = []
                for item in photos:
                    image_path = store.folder / f'{item["id"]}.png'
                    image = read_image(image_path, formats=("PNG",))
                    engine, engine_detail = detect_engine()
                    log_file = write_ai_log(run_id, {
                        "event": "photo_start", "batch_index": batch_index,
                        "batch_total": batch_total, "photo_id": item["id"],
                        "photo_name": item["name"], "image_size": list(image.size),
                        "engine": engine, "engine_detail": engine_detail,
                    })
                    ai_lock = getattr(self.server, "ai_lock", None)
                    if ai_lock:
                        with ai_lock:
                            result = analyze(image, item["id"], store.state.get("product", "商品"), ROOT / "cache", self.server.model_identity)
                    else:
                        result = analyze(image, item["id"], store.state.get("product", "商品"), ROOT / "cache", self.server.model_identity if hasattr(self.server, "model_identity") else MODEL)
                    if run_id in getattr(self.server, "ai_cancelled", set()):
                        self.send({"cancelled": True, "run_id": run_id, "results": []})
                        return
                    analysis = result.get("analysis")
                    crop_box = list(top_only_crop_box(analysis, image.size)) if analysis else [0, 0, image.width, image.height]
                    issues = list(result.get("issues", []))
                    unsafe = {
                        "上緣裁線過近或切入商品，保留原圖待確認",
                        "上緣裁線過高，頭部上半部未完整移除，保留原圖待確認",
                    }
                    if analysis and crop_box[1] > 0 and unsafe.intersection(issues):
                        issues = [issue for issue in issues if issue not in unsafe]
                        issues.append("模型裁線不安全，已改用保守裁線並保留下半臉")
                    if not analysis:
                        outcome = "failed"
                    elif crop_box[1] <= 0:
                        outcome = "no_crop"
                    elif issues:
                        outcome = "review"
                    else:
                        outcome = "safe"
                    log_file = write_ai_log(run_id, {
                        "event": "photo_finish", "batch_index": batch_index,
                        "batch_total": batch_total, "photo_id": item["id"],
                        "photo_name": item["name"], "outcome": outcome,
                        "crop_top": crop_box[1], "issues": issues,
                        "cache_hit": result.get("cache_hit", False),
                        "seconds": result.get("seconds_this_run", 0),
                        "attempts": [_ai_attempt_summary(attempt) for attempt in result.get("attempts", [])],
                    }) or log_file
                    results.append({"id": item["id"], "name": item["name"], "analysis": analysis,
                                    "issues": issues, "cache_hit": result.get("cache_hit", False),
                                    "seconds": result.get("seconds_this_run", 0), "crop_box": crop_box,
                                    "model": MODEL})
                if batch_total is not None and batch_index == batch_total:
                    log_file = write_ai_log(run_id, {
                        "event": "run_complete", "batch_total": batch_total,
                    }) or log_file
                self.send({"results": results, "model": MODEL, "run_id": run_id,
                           "log_file": log_file})
            elif route.path == "/api/export":
                result = store.export()
                self.server.exports.add(result["batch"])
                self.send(result)
            elif route.path == "/api/reset":
                cleanup = data.get("cleanup", False)
                removed = store.reset(cleanup=cleanup)
                self.send({"reset": True, "removed_files": removed})
            elif route.path == "/api/open-export":
                batch = data.get("batch")
                if batch not in self.server.exports:
                    raise ValueError("請先匯出拼圖")
                import sys
                if sys.platform == "win32":
                    os.startfile(str(store.output / batch))
                elif sys.platform == "darwin":
                    subprocess.Popen(["/usr/bin/open", str(store.output / batch)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.Popen(["xdg-open", str(store.output / batch)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.send({"opened": True})
            else:
                self.send({"error": "不支援此操作"}, status=404)
        except (OSError, ValueError, KeyError, TypeError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            error = "照片像素過高，請使用較小的照片或先轉存一份工作檔" if isinstance(exc, (Image.DecompressionBombError, Image.DecompressionBombWarning)) else str(exc)
            self.send({"error": error}, status=400)


class StudioHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    # Auto-open browser when running as PyInstaller bundle
    if getattr(sys, 'frozen', False) and not args.open:
        args.open = True
    try:
        server = StudioHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        if args.open:
            try:
                connection = http.client.HTTPConnection("127.0.0.1", args.port, timeout=2)
                connection.request("GET", "/api/health")
                response = connection.getresponse()
                health = json.loads(response.read(1000))
                connection.close()
                if response.status == 200 and health.get("app") == "local-clothing-pairs-v1":
                    webbrowser.open(f"http://127.0.0.1:{args.port}")
                    return
            except (OSError, ValueError, http.client.HTTPException):
                pass
        raise SystemExit(f"{args.port} 連接埠已被其他程式使用，請先關閉佔用程式或指定另一個 port")
    server.store = StudioStore()
    server.token = secrets.token_urlsafe(32)
    server.exports = set()
    server.ai_lock = threading.Lock()
    server.ai_cancelled = OrderedDict()
    server.model_identity = f"{MODEL}:builtin"
    server.timeout = 30
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"服飾圖片工作室：{url}\n輸出位置：{server.store.output}\n關閉這個執行視窗可停止工具", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
