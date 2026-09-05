"""Deterministic local clothing-photo studio; no model or network calls."""
from __future__ import annotations

import hashlib
import io
import json
from collections import OrderedDict
from pathlib import Path
import re
import tempfile
import threading
import time

from PIL import Image, ImageCms

from garment_pilot import ROOT, atomic_json, read_image

SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
SRGB_BYTES = SRGB.tobytes()
FORMATS = {"square", "natural"}
INPUT_FORMATS = ("JPEG", "PNG", "WEBP", "AVIF", "BMP", "TIFF", "GIF")
INPUT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".tif", ".tiff", ".gif"}
EXPORT_VERSION = "jpeg95-v2-source-hash"
PREVIEW_MAX_EDGE = 1600


def render_pair(images, mode="square"):
    if mode not in FORMATS or len(images) != 2:
        raise ValueError("請選兩張照片與有效的輸出比例")
    widths = [im.width for im in images]
    heights = [im.height for im in images]
    content_width, content_height = sum(widths), max(heights)
    if mode == "square":
        side = max(content_width, content_height)
        size = (side, side)
        x, y = (side - content_width) // 2, (side - content_height) // 2
    else:
        size = (content_width, content_height)
        x, y = 0, 0
    canvas = Image.new("RGB", size, "white")
    placements = []
    for im, width in zip(images, widths):
        canvas.paste(im, (x, y + (content_height - im.height if mode == "natural" else 0)))
        placements.append([x, y + (content_height - im.height if mode == "natural" else 0), width, im.height])
        x += width
    return canvas, {"size": list(size), "placements": placements, "upscaled": False,
                    "padding": size != (content_width, content_height),
                    "low_resolution": content_width < 1024 or content_height < 1024}


def jpeg_bytes(image):
    data = io.BytesIO()
    image.save(data, "JPEG", quality=95, icc_profile=SRGB_BYTES)
    return data.getvalue(), 95


class StudioStore:
    def __init__(self, folder=None, output=None):
        self.folder = Path(folder or ROOT / "studio-data")
        self.output = Path(output or ROOT / "outputs")
        self.folder.mkdir(parents=True, exist_ok=True)
        self.output.mkdir(parents=True, exist_ok=True)
        self._crop_cache = OrderedDict()
        self._crop_cache_bytes = 0
        self._lock = threading.RLock()
        self.state_file = self.folder / "state.json"
        self.last_export_file = self.folder / "last-export.json"
        self.state = {"product": "這一件商品", "photos": [], "groups": [], "format": "natural", "ai_crops": {}, "photo_crops": {}}
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text())
            self.state.setdefault("ai_crops", {})
            self.state.setdefault("photo_crops", {})
            if self._migrate_legacy_crops():
                self.save()

    def save(self):
        atomic_json(self.state_file, self.state)

    def _migrate_legacy_crops(self):
        changed = False
        photos = {item["id"]: item for item in self.state.get("photos", [])}
        old_bounds = {}
        for photo in photos.values():
            full = [0, photo["width"]]
            old_bounds[photo["id"]] = photo.get("white", full)
            if photo.get("white") != full:
                photo["white"] = full
                changed = True
        for group in self.state.get("groups", []):
            for key in ("left", "right"):
                side = group.get(key, {})
                if set(side) != {"photo", "top", "cut"} or side.get("photo") not in photos:
                    continue
                photo = photos[side["photo"]]
                top = side["top"] if side["cut"] else 0
                side.clear()
                side.update(photo=photo["id"], box=[photo["white"][0], top, photo["white"][1], photo["height"]])
                changed = True
        for group in self.state.get("groups", []):
            for key in ("left", "right"):
                side = group.get(key, {})
                photo = photos.get(side.get("photo"))
                old = old_bounds.get(side.get("photo"))
                if not photo or not old or set(side) != {"photo", "box"}:
                    continue
                if side["box"][0] == old[0] and side["box"][2] == old[1]:
                    side["box"][0], side["box"][2] = 0, photo["width"]
                    changed = True
        return changed

    def photo(self, photo_id):
        for photo in self.state["photos"]:
            if photo["id"] == photo_id:
                return photo
        raise ValueError("照片不存在，請重新匯入")

    def _remove_photo_files(self, photo_id):
        for suffix in (".png", "-thumb.jpg"):
            try:
                (self.folder / f"{photo_id}{suffix}").unlink()
            except FileNotFoundError:
                pass

    def _remove_all_photo_files(self):
        removed = 0
        for pattern in ("*.png", "*-thumb.jpg"):
            for path in self.folder.glob(pattern):
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def delete_photo(self, photo_id):
        return self.delete_photos([photo_id])[0]

    def delete_photos(self, photo_ids):
        if not isinstance(photo_ids, list) or not photo_ids or len(photo_ids) > 100 or len(set(photo_ids)) != len(photo_ids):
            raise ValueError("照片清單格式無效")
        photos = [self.photo(photo_id) for photo_id in photo_ids]
        paired = {side["photo"] for group in self.state["groups"] for side in (group["left"], group["right"])}
        if paired.intersection(photo_ids):
            raise ValueError("已配對的照片不能直接刪除，請先解除配對")
        self.state["photos"] = [item for item in self.state["photos"] if item["id"] not in photo_ids]
        ai_crops = self.state.setdefault("ai_crops", {})
        for photo_id in photo_ids:
            ai_crops.pop(photo_id, None)
            self.state.setdefault("photo_crops", {}).pop(photo_id, None)
            self._remove_photo_files(photo_id)
        self._crop_cache = OrderedDict(
            (key, value) for key, value in self._crop_cache.items() if key[0] not in photo_ids
        )
        self._crop_cache_bytes = sum(image.width * image.height * 3 for image, _ in self._crop_cache.values())
        self.save()
        return photos

    def reset(self, cleanup=False):
        if not isinstance(cleanup, bool):
            raise ValueError("清理選項格式無效")
        removed_files = self._remove_all_photo_files() if cleanup else 0
        self.state.update(product="這一件商品", photos=[], groups=[], format="natural", ai_crops={}, photo_crops={})
        self._crop_cache.clear()
        self._crop_cache_bytes = 0
        self.save()
        return removed_files

    def import_photo(self, name, raw):
        if not raw or len(raw) > 40_000_000:
            raise ValueError("每張照片須小於 40 MB")
        if not isinstance(name, str) or len(name) > 240 or Path(name).suffix.lower() not in INPUT_EXTENSIONS:
            raise ValueError("支援 JPG/JPEG、PNG、WebP、AVIF、BMP、TIFF 與靜態 GIF")
        name = name.replace("\\", "/").split("/")[-1]
        photo_id = hashlib.sha256(raw).hexdigest()
        if any(p["id"] == photo_id for p in self.state["photos"]):
            return {"duplicate": True, "photo": self.photo(photo_id)}
        if len(self.state["photos"]) >= 100:
            raise ValueError("一件商品最多匯入 100 張照片，請先開始新商品")
        with tempfile.NamedTemporaryFile(dir=self.folder, suffix=".incoming") as source:
            source.write(raw)
            source.flush()
            image = read_image(Path(source.name), formats=INPUT_FORMATS)
        if image.info.get("icc_profile"):
            try:
                image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(image.info["icc_profile"])), SRGB, outputMode="RGB")
            except (OSError, ValueError, ImageCms.PyCMSError) as exc:
                raise ValueError("照片色彩描述檔無法讀取，請先轉存為 sRGB") from exc
        image.info["icc_profile"] = SRGB_BYTES
        bounds = [0, image.width]
        image.save(self.folder / f"{photo_id}.png")
        thumb = image.copy()
        thumb.thumbnail((800, 800), Image.Resampling.LANCZOS)
        thumb.save(self.folder / f"{photo_id}-thumb.jpg", quality=90, icc_profile=SRGB_BYTES)
        meta = {"id": photo_id, "name": name, "width": image.width, "height": image.height,
                "white": bounds}
        self.state["photos"].append(meta)
        self.save()
        return {"duplicate": False, "photo": meta}

    def validate_side(self, side):
        if not isinstance(side, dict):
            raise ValueError("配對資料不完整")
        photo = self.photo(side.get("photo"))
        if set(side) == {"photo", "top", "cut"}:
            if type(side["top"]) is not int or not 0 <= side["top"] < photo["height"] or type(side["cut"]) is not bool:
                raise ValueError("裁切範圍超出照片")
            box = [photo["white"][0], side["top"] if side["cut"] else 0, photo["white"][1], photo["height"]]
        elif set(side) == {"photo", "box"} and isinstance(side["box"], list) and len(side["box"]) == 4:
            box = side["box"]
        else:
            raise ValueError("配對資料不完整")
        if any(type(value) is not int for value in box):
            raise ValueError("裁切範圍格式無效")
        left, top, right, bottom = box
        if not (photo["white"][0] <= left < right <= photo["white"][1] and 0 <= top < bottom <= photo["height"]):
            raise ValueError("裁切範圍超出照片")
        if right - left < 20 or bottom - top < 20:
            raise ValueError("裁切範圍太窄，至少要保留 20 個像素")
        return {"photo": photo["id"], "box": [left, top, right, bottom]}

    def validate_group(self, group):
        if not isinstance(group, dict) or not set(group).issubset({"id", "left", "right", "preview_crop"}) or set(group) not in ({"id", "left", "right"}, {"id", "left", "right", "preview_crop"}) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", str(group["id"])):
            raise ValueError("配對格式無效")
        left, right = self.validate_side(group["left"]), self.validate_side(group["right"])
        if left["photo"] == right["photo"]:
            raise ValueError("請選擇兩張不同的照片")
        result = {"id": group["id"], "left": left, "right": right}
        if "preview_crop" in group:
            box = group["preview_crop"]
            if not isinstance(box, list) or len(box) != 4 or any(type(value) is not int for value in box) or not (0 <= box[0] < box[2] <= 10000 and 0 <= box[1] < box[3] <= 10000):
                raise ValueError("預覽裁切範圍格式無效")
            result["preview_crop"] = box
        return result

    def update(self, data):
        if not isinstance(data, dict) or set(data) not in ({"product", "groups", "format"}, {"product", "groups", "format", "ai_crops"}, {"product", "groups", "format", "photo_crops"}, {"product", "groups", "format", "ai_crops", "photo_crops"}):
            raise ValueError("工作資料格式無效")
        if not isinstance(data["product"], str) or len(data["product"]) > 80 or data["format"] not in FORMATS:
            raise ValueError("商品名稱或輸出比例無效")
        if not isinstance(data["groups"], list) or len(data["groups"]) > 50:
            raise ValueError("一次最多輸出 50 組")
        groups = [self.validate_group(g) for g in data["groups"]]
        if len({g["id"] for g in groups}) != len(groups):
            raise ValueError("配對編號重複")
        ai_crops = data.get("ai_crops", {})
        if not isinstance(ai_crops, dict):
            raise ValueError("AI 裁切資料格式無效")
        photo_ids = {p["id"] for p in self.state["photos"]}
        if any(photo_id not in photo_ids or type(top) is not int or not 0 <= top < self.photo(photo_id)["height"] for photo_id, top in ai_crops.items()):
            raise ValueError("AI 裁切資料無效")
        photo_crops = data.get("photo_crops", self.state.get("photo_crops", {}))
        if not isinstance(photo_crops, dict):
            raise ValueError("照片裁切資料格式無效")
        for photo_id, box in photo_crops.items():
            if photo_id not in photo_ids or not isinstance(box, list) or len(box) != 4 or any(type(value) is not int for value in box):
                raise ValueError("照片裁切資料無效")
            photo = self.photo(photo_id)
            if not (photo["white"][0] <= box[0] < box[2] <= photo["white"][1] and 0 <= box[1] < box[3] <= photo["height"]):
                raise ValueError("照片裁切範圍超出照片")
            if box[2] - box[0] < 20 or box[3] - box[1] < 20:
                raise ValueError("照片裁切範圍太窄，至少要保留 20 個像素")
        self.state.update(product=data["product"], format=data["format"], groups=groups, ai_crops=ai_crops, photo_crops=photo_crops)
        self.save()

    def crop(self, side, cache=True):
        side = self.validate_side(side)
        photo = self.photo(side["photo"])
        key = (photo["id"], tuple(side["box"]))
        if cache and key in self._crop_cache:
            cached, info = self._crop_cache[key]
            return cached.copy(), dict(info)
        with Image.open(self.folder / f'{photo["id"]}.png') as image:
            box = side["box"]
            cropped = image.crop(box)
        info = {"name": photo["name"], "sha256": photo["id"], "crop_box": box}
        if cache:
            entry_bytes = cropped.width * cropped.height * 3
            if entry_bytes <= 16_000_000:
                while self._crop_cache and self._crop_cache_bytes + entry_bytes > 64_000_000:
                    _, (old, _) = self._crop_cache.popitem(last=False)
                    self._crop_cache_bytes -= old.width * old.height * 3
                self._crop_cache[key] = (cropped.copy(), info)
                self._crop_cache_bytes += entry_bytes
        return cropped, info

    def compose(self, group, mode):
        group = self.validate_group(group)
        left, ls = self.crop(group["left"])
        right, rs = self.crop(group["right"])
        image, info = render_pair([left, right], mode)
        if "preview_crop" in group:
            values = group["preview_crop"]
            left_px = round(image.width * values[0] / 10000)
            top_px = round(image.height * values[1] / 10000)
            right_px = round(image.width * values[2] / 10000)
            bottom_px = round(image.height * values[3] / 10000)
            if right_px - left_px < 20 or bottom_px - top_px < 20:
                raise ValueError("預覽裁切範圍太窄，至少要保留 20 個像素")
            image = image.crop((left_px, top_px, right_px, bottom_px))
            info = {**info, "size": list(image.size), "preview_crop": values}
        return image, {**info, "sources": [ls, rs]}

    def compose_preview(self, group, mode, max_edge=PREVIEW_MAX_EDGE):
        """Compose a bounded preview without changing full-resolution export data."""
        group = self.validate_group(group)
        left, ls = self.crop(group["left"], cache=False)
        right, rs = self.crop(group["right"], cache=False)
        widths = [left.width, right.width]
        heights = [left.height, right.height]
        content_width, content_height = sum(widths), max(heights)
        target = max(content_width, content_height) if mode == "square" else max(content_width, content_height)
        scale = min(1, max_edge / max(1, target))
        if scale < 1:
            left = left.resize((max(1, round(left.width * scale)), max(1, round(left.height * scale))), Image.Resampling.LANCZOS)
            right = right.resize((max(1, round(right.width * scale)), max(1, round(right.height * scale))), Image.Resampling.LANCZOS)
        image, info = render_pair([left, right], mode)
        if "preview_crop" in group:
            values = group["preview_crop"]
            left_px = round(image.width * values[0] / 10000)
            top_px = round(image.height * values[1] / 10000)
            right_px = round(image.width * values[2] / 10000)
            bottom_px = round(image.height * values[3] / 10000)
            if right_px - left_px < 20 or bottom_px - top_px < 20:
                raise ValueError("預覽裁切範圍太窄，至少要保留 20 個像素")
            image = image.crop((left_px, top_px, right_px, bottom_px))
            info = {**info, "preview_crop": values}
        return image, {**info, "sources": [ls, rs], "preview": True}

    def photo_preview(self, side, max_edge=PREVIEW_MAX_EDGE):
        image, info = self.crop(side, cache=False)
        source_size = list(image.size)
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        return image, {**info, "size": list(image.size), "source_size": source_size, "preview": True}

    def _export_signature(self):
        used = {side["photo"] for group in self.state["groups"] for side in (group["left"], group["right"])}
        photos = []
        for photo in self.state["photos"]:
            if photo["id"] in used:
                continue
            box = self.state.get("photo_crops", {}).get(photo["id"])
            if not box:
                top = self.state.get("ai_crops", {}).get(photo["id"], 0)
                box = [0, top, photo["width"], photo["height"]]
            photos.append([photo["id"], box])
        groups = [
            {
                "left": group["left"],
                "right": group["right"],
                **({"preview_crop": group["preview_crop"]} if "preview_crop" in group else {}),
            }
            for group in self.state["groups"]
        ]
        payload = {"version": EXPORT_VERSION, "format": self.state["format"], "photos": photos, "groups": groups,
                   "source_hashes": {photo["id"]: photo["id"] for photo in self.state["photos"]}}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _previous_export(self, signature):
        try:
            saved = json.loads(self.last_export_file.read_text())
            result = saved["result"]
            run = Path(result["folder"])
            if saved["signature"] != signature or run.name != result["batch"] or run.parent.resolve() != self.output.resolve():
                return None
            for item in result["results"]:
                filename = item["file"]
                path = run / filename
                if Path(filename).name != filename or not path.is_file() or path.stat().st_size != item["bytes"]:
                    return None
            return {**result, "reused": True}
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def export(self):
        if not self.state["photos"] and not self.state["groups"]:
            raise ValueError("先匯入照片")
        signature = self._export_signature()
        previous = self._previous_export(signature)
        if previous:
            return previous
        run = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=self.output))
        records = []
        used_photo_ids = {side["photo"] for group in self.state["groups"] for side in (group["left"], group["right"])}
        standalone_records = []
        for index, photo in enumerate(self.state["photos"], 1):
            if photo["id"] in used_photo_ids:
                continue
            image = read_image(self.folder / f'{photo["id"]}.png', formats=("PNG",))
            crop_box = self.state.get("photo_crops", {}).get(photo["id"])
            top = self.state.get("ai_crops", {}).get(photo["id"], 0)
            if crop_box:
                image = image.crop(crop_box)
            elif top:
                crop_box = [0, top, image.width, image.height]
                image = image.crop(crop_box)
            else:
                crop_box = [0, 0, image.width, image.height]
            data, quality = jpeg_bytes(image)
            filename = f"照片-{len(standalone_records) + 1:03d}.jpg"
            (run / filename).write_bytes(data)
            standalone_records.append({"type": "photo", "file": filename, "bytes": len(data),
                                       "jpeg_quality": quality, "name": photo["name"], "sha256": photo["id"],
                                       "crop_box": crop_box,
                                       "ai_crop": bool(top)})
        records.extend(standalone_records)
        for i, group in enumerate(self.state["groups"], 1):
            image, info = self.compose(group, self.state["format"])
            data, quality = jpeg_bytes(image)
            filename = f"拼圖-{i:03d}.jpg"
            (run / filename).write_bytes(data)
            records.append({"type": "collage", "group_id": group["id"], "file": filename,
                            "bytes": len(data), "jpeg_quality": quality, **info})
        result = {"batch": run.name, "folder": str(run), "count": len(records),
                  "photo_count": len(standalone_records), "collage_count": len(self.state["groups"]),
                  "results": records, "reused": False}
        atomic_json(self.last_export_file, {"signature": signature, "result": result})
        return result
