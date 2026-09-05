import copy
import http.client
from http.server import HTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest

from PIL import Image, ImageDraw

from studio_core import StudioStore, render_pair
from studio_server import Handler


def picture(color, size=(180, 300), white=False):
    image = Image.new("RGB", size, color)
    if white:
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 19, size[1] - 1), fill="white")
        draw.rectangle((size[0] - 20, 0, size[0] - 1, size[1] - 1), fill="white")
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StudioStore(self.root / "data", self.root / "outputs")
        self.store.cached_cut = lambda *args: (0, "test")
        self.a = self.store.import_photo("中文左圖.png", picture("red", white=True))["photo"]
        self.b = self.store.import_photo("右圖.png", picture("blue", (240, 400)))["photo"]
        self.group = {"id": "group-1", "left": {"photo": self.a["id"], "top": 50, "cut": True}, "right": {"photo": self.b["id"], "top": 0, "cut": False}}

    def tearDown(self):
        self.temp.cleanup()

    def test_duplicate_and_corrupt_file(self):
        self.assertTrue(self.store.import_photo("另一名字.png", picture("red", white=True))["duplicate"])
        self.assertEqual(len(self.store.state["photos"]), 2)
        with self.assertRaises(OSError):
            self.store.import_photo("損壞.jpg", b"not a photo")
        with self.assertRaises(ValueError):
            self.store.import_photo("photo.svg", picture("red"))

    def test_reset_can_clean_working_photo_copies(self):
        self.assertTrue((self.store.folder / f'{self.a["id"]}.png').is_file())
        self.assertTrue((self.store.folder / f'{self.a["id"]}-thumb.jpg').is_file())
        removed = self.store.reset(cleanup=True)
        self.assertEqual(removed, 4)
        self.assertFalse((self.store.folder / f'{self.a["id"]}.png').exists())
        self.assertFalse((self.store.folder / f'{self.a["id"]}-thumb.jpg').exists())
        self.assertEqual(self.store.state["photos"], [])

    def test_delete_unpaired_photo_removes_working_files_and_preserves_pairs(self):
        self.store.state["ai_crops"] = {self.a["id"]: 40}
        deleted = self.store.delete_photo(self.a["id"])
        self.assertEqual(deleted["id"], self.a["id"])
        self.assertNotIn(self.a["id"], [photo["id"] for photo in self.store.state["photos"]])
        self.assertNotIn(self.a["id"], self.store.state["ai_crops"])
        self.assertFalse((self.store.folder / f'{self.a["id"]}.png').exists())
        self.assertFalse((self.store.folder / f'{self.a["id"]}-thumb.jpg').exists())
        self.store.state["groups"] = [self.group]
        with self.assertRaisesRegex(ValueError, "不能直接刪除"):
            self.store.delete_photo(self.b["id"])

    def test_import_preserves_full_photo_width(self):
        imported = self.store.import_photo("白邊仍保留.png", picture("#dddddd", white=True))["photo"]
        self.assertEqual(imported["white"], [0, imported["width"]])
        with Image.open(self.store.folder / f'{imported["id"]}.png') as image:
            self.assertEqual(image.size, (180, 300))

    def test_common_static_formats_import_and_export(self):
        for extension, fmt in [("JPEG", "JPEG"), ("webp", "WEBP"), ("avif", "AVIF"), ("bmp", "BMP"), ("tif", "TIFF"), ("tiff", "TIFF"), ("gif", "GIF")]:
            with self.subTest(format=fmt, extension=extension):
                raw = io.BytesIO()
                Image.new("RGB", (80, 120), "green").save(raw, fmt)
                result = self.store.import_photo(f"中文測試.{extension}", raw.getvalue())
                self.assertEqual(result["photo"]["width"], 80)
                group = {**self.group, "left": {"photo": result["photo"]["id"], "top": 0, "cut": False}}
                self.store.update({"product": "", "groups": [group], "format": "natural"})
                exported = self.store.export()
                with Image.open(Path(exported["folder"]) / "照片-001.jpg") as image:
                    self.assertEqual(image.format, "JPEG")

    def test_animated_multi_page_and_disguised_images_are_rejected(self):
        count = len(self.store.state["photos"])
        for fmt, extension in [("GIF", "gif"), ("WEBP", "webp"), ("TIFF", "tiff"), ("PNG", "png")]:
            raw = io.BytesIO()
            Image.new("RGB", (20, 30), "red").save(raw, fmt, save_all=True, append_images=[Image.new("RGB", (20, 30), "blue")])
            with self.subTest(format=fmt), self.assertRaisesRegex(ValueError, "動畫或多頁"):
                self.store.import_photo(f"多張.{extension}", raw.getvalue())
        for raw in (b"<svg></svg>", b"%PDF-1.0 not an image", b"broken webp"):
            with self.assertRaises((OSError, ValueError)):
                self.store.import_photo("偽裝.webp", raw)
        self.assertEqual(len(self.store.state["photos"]), count)

    def test_crop_preserves_pixels_bottom_and_real_background(self):
        crop, info = self.store.crop(self.group["left"])
        with Image.open(self.store.folder / f'{self.a["id"]}.png') as original:
            self.assertEqual(info["crop_box"], [0, 50, 180, 300])
            self.assertEqual(crop.tobytes(), original.crop((0, 50, 180, 300)).tobytes())
            crop, info = self.store.crop({**self.group["left"], "cut": False})
            self.assertEqual(crop.tobytes(), original.crop((0, 0, 180, 300)).tobytes())

    def test_natural_layout_no_padding_no_upscale_and_correct_sides(self):
        image, info = self.store.compose(self.group, "natural")
        self.assertFalse(info["padding"])
        self.assertFalse(info["upscaled"])
        self.assertEqual(image.size, (420, 400))
        self.assertEqual(image.getpixel((0, 149)), (255, 255, 255))
        self.assertEqual(image.getpixel((20, 150)), (255, 0, 0))
        self.assertEqual(image.getpixel((image.width - 1, image.height - 1)), (0, 0, 255))
        self.assertEqual(info["placements"][0], [0, 150, 180, 250])
        self.assertEqual(info["placements"][1], [180, 0, 240, 400])
        self.assertEqual(sum(p[2] for p in info["placements"]), image.width)
        swapped = {**self.group, "left": self.group["right"], "right": self.group["left"]}
        other, _ = self.store.compose(swapped, "natural")
        self.assertEqual(other.getpixel((0, 0)), (0, 0, 255))

    def test_export_reuses_unchanged_batch_and_recreates_missing_output(self):
        self.store.update({"product": "中文裙子", "groups": [self.group], "format": "square"})
        first, second = self.store.export(), self.store.export()
        self.assertEqual(first["folder"], second["folder"])
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        path = Path(first["folder"])
        with Image.open(path / "拼圖-001.jpg") as image:
            self.assertEqual(image.size, (420, 420))
        self.assertGreater((path / "拼圖-001.jpg").stat().st_size, 0)
        self.assertFalse((path / "配對與裁切紀錄.json").exists())
        self.assertFalse((path / "所有圖片.zip").exists())
        self.assertEqual(first["results"][0]["jpeg_quality"], 95)
        self.assertEqual(first["results"][0]["sources"][0]["name"], "中文左圖.png")
        self.assertEqual(first["photo_count"], 0)
        self.assertEqual(first["collage_count"], 1)
        (path / "拼圖-001.jpg").unlink()
        recreated = self.store.export()
        self.assertNotEqual(first["folder"], recreated["folder"])
        self.assertFalse(recreated["reused"])
        restored = StudioStore(self.store.folder, self.store.output)
        self.assertEqual(restored.export()["folder"], recreated["folder"])
        changed = copy.deepcopy(self.group)
        changed["left"]["top"] = 51
        restored.update({"product": "中文裙子", "groups": [changed], "format": "square"})
        self.assertNotEqual(restored.export()["folder"], recreated["folder"])

    def test_export_includes_unused_photos_and_ai_crop_without_duplicate_sources(self):
        self.store.update({"product": "照片匯出", "groups": [], "format": "natural", "ai_crops": {self.a["id"]: 50}})
        exported = self.store.export()
        path = Path(exported["folder"])
        self.assertEqual(exported["count"], 2)
        self.assertEqual(exported["photo_count"], 2)
        self.assertEqual(exported["collage_count"], 0)
        with Image.open(path / "照片-001.jpg") as image:
            self.assertEqual(image.height, self.a["height"] - 50)
        self.assertTrue((path / "照片-002.jpg").is_file())
        self.assertFalse(any(path.glob("拼圖-*.jpg")))

    def test_single_photo_crop_is_persisted_and_exported(self):
        box = [10, 20, 170, 280]
        self.store.update({"product": "單張裁切", "groups": [], "format": "natural", "photo_crops": {self.a["id"]: box}})
        restored = StudioStore(self.store.folder, self.store.output)
        self.assertEqual(restored.state["photo_crops"][self.a["id"]], box)
        exported = restored.export()
        with Image.open(Path(exported["folder"]) / "照片-001.jpg") as image:
            self.assertEqual(image.size, (160, 260))

    def test_preview_crop_is_saved_and_applied_after_studio(self):
        group = {**self.group, "preview_crop": [1000, 1000, 9000, 9000]}
        self.store.update({"product": "test", "groups": [group], "format": "square"})
        image, info = self.store.compose(group, "square")
        self.assertEqual(image.size, (336, 336))
        self.assertEqual(info["preview_crop"], [1000, 1000, 9000, 9000])
        restored = StudioStore(self.store.folder, self.store.output)
        self.assertEqual(restored.state["groups"][0]["preview_crop"], [1000, 1000, 9000, 9000])
        for invalid in ([0, 0, 10001, 10000], [0, 1.5, 9000, 9000]):
            with self.assertRaises(ValueError):
                self.store.update({"product": "test", "groups": [{**self.group, "preview_crop": invalid}], "format": "square"})
        tiny = {**self.group, "preview_crop": [0, 0, 2, 10000]}
        self.store.update({"product": "test", "groups": [tiny], "format": "square"})
        with self.assertRaisesRegex(ValueError, "預覽裁切範圍太窄"):
            self.store.compose(tiny, "square")

    def test_persistence_and_invalid_coordinates(self):
        self.store.update({"product": "test", "groups": [self.group], "format": "natural"})
        restored = StudioStore(self.store.folder, self.store.output)
        self.assertEqual(restored.state, self.store.state)
        before = copy.deepcopy(self.store.state)
        for top in [-1, 300, True, 1.5]:
            invalid = copy.deepcopy(self.group)
            invalid["left"]["top"] = top
            with self.assertRaises(ValueError):
                self.store.update({"product": "changed", "groups": [invalid], "format": "square"})
        self.assertEqual(self.store.state, before)
        invalid = {**self.group, "right": self.group["left"]}
        with self.assertRaises(ValueError):
            self.store.compose(invalid, "square")

    def test_low_resolution_is_not_enlarged(self):
        im, info = render_pair([Image.new("RGB", (20, 30)), Image.new("RGB", (40, 30))], "natural")
        self.assertEqual(im.size, (60, 30))
        self.assertTrue(info["low_resolution"])
        self.assertFalse(info["upscaled"])

    def test_extreme_dimensions_are_preserved_instead_of_clipped(self):
        image, info = render_pair([Image.new("RGB", (1100, 1)), Image.new("RGB", (733, 999))], "square")
        self.assertEqual(image.size, (1833, 1833))
        self.assertFalse(info["upscaled"])

    def test_compose_preview_downscales_large_output(self):
        big_a = self.store.import_photo("大圖A.png", picture("red", (2000, 3000)))["photo"]
        big_b = self.store.import_photo("大圖B.png", picture("blue", (2000, 3000)))["photo"]
        group = {"id": "big", "left": {"photo": big_a["id"], "top": 0, "cut": False}, "right": {"photo": big_b["id"], "top": 0, "cut": False}}
        self.store.update({"product": "大圖", "groups": [group], "format": "natural"})
        full_image, full_info = self.store.compose(group, "natural")
        preview_image, preview_info = self.store.compose_preview(group, "natural")
        self.assertEqual(full_image.size, (4000, 3000))
        self.assertLessEqual(max(preview_image.size), 1600)
        self.assertTrue(preview_info.get("preview"))
        max_full = max(full_image.size)
        self.assertGreater(max_full, 1600)

    def test_photo_preview_downscales_andReportsSourceSize(self):
        imported = self.store.import_photo("預覽測試.png", picture("green", (3000, 2000)))["photo"]
        side = {"photo": imported["id"], "box": [0, 0, imported["width"], imported["height"]]}
        image, info = self.store.photo_preview(side)
        self.assertLessEqual(max(image.size), 1600)
        self.assertEqual(info["source_size"], [3000, 2000])
        self.assertEqual(info["size"], list(image.size))
        self.assertTrue(info.get("preview"))

    def test_photo_preview_does_not_enlarge_small_image(self):
        small = self.store.import_photo("小圖.png", picture("yellow", (100, 80)))["photo"]
        side = {"photo": small["id"], "box": [0, 0, small["width"], small["height"]]}
        image, info = self.store.photo_preview(side)
        self.assertEqual(image.size, (100, 80))
        self.assertEqual(info["source_size"], [100, 80])

    def test_export_preserves_original_dimensions_not_preview_size(self):
        self.store.reset(cleanup=True)
        big = self.store.import_photo("原圖大.png", picture("red", (2400, 1800)))["photo"]
        self.store.update({"product": "原尺寸匯出", "groups": [], "format": "natural"})
        preview_img, _ = self.store.photo_preview({"photo": big["id"], "box": [0, 0, big["width"], big["height"]]})
        self.assertLessEqual(max(preview_img.size), 1600)
        exported = self.store.export()
        with Image.open(Path(exported["folder"]) / "照片-001.jpg") as image:
            self.assertEqual(image.size, (2400, 1800))

    def test_export_signature_includes_crop_state(self):
        self.store.update({"product": "指紋測試", "groups": [], "format": "natural"})
        sig_before = self.store._export_signature()
        self.store.update({"product": "指紋測試", "groups": [], "format": "natural", "photo_crops": {self.a["id"]: [10, 20, 170, 280]}})
        sig_after = self.store._export_signature()
        self.assertNotEqual(sig_before, sig_after)

    def test_export_cache_invalidated_by_crop_change(self):
        self.store.update({"product": "快取測試", "groups": [self.group], "format": "natural"})
        first = self.store.export()
        self.assertFalse(first["reused"])
        second = self.store.export()
        self.assertTrue(second["reused"])
        changed = copy.deepcopy(self.group)
        changed["left"]["top"] = 60
        self.store.update({"product": "快取測試", "groups": [changed], "format": "natural"})
        third = self.store.export()
        self.assertFalse(third["reused"])
        self.assertNotEqual(first["folder"], third["folder"])

    def test_apply_ai_crops_to_deleted_photo_does_not_crash(self):
        ai_crops = {self.a["id"]: 40, self.b["id"]: 30}
        self.store.update({"product": "刪除後套用", "groups": [], "format": "natural", "ai_crops": ai_crops})
        self.store.delete_photo(self.a["id"])
        restored = StudioStore(self.store.folder, self.store.output)
        self.assertNotIn(self.a["id"], restored.state.get("ai_crops", {}))
        self.assertIn(self.b["id"], restored.state["ai_crops"])
        exported = restored.export()
        self.assertEqual(exported["photo_count"], 1)

    def test_apply_ai_crops_skips_deleted_photos(self):
        self.store.update({"product": "不存在", "groups": [], "format": "natural", "ai_crops": {self.a["id"]: 50}})
        self.store.delete_photo(self.a["id"])
        exported = self.store.export()
        self.assertEqual(exported["photo_count"], 1)

    def test_oversized_photo_returns_helpful_error(self):
        import struct
        fake_jpg = b'\xff\xd8\xff\xe0' + b'\x00' * 100
        with self.assertRaises((OSError, ValueError)):
            self.store.import_photo("假大圖.jpg", fake_jpg)

    def test_decompression_bomb_is_caught(self):
        import struct
        tiny = io.BytesIO()
        Image.new("RGB", (10, 10)).save(tiny, "PNG")
        imported = self.store.import_photo("正常小圖.png", tiny.getvalue())["photo"]
        self.assertEqual(imported["width"], 10)

    def test_server_local_host_csrf_and_path_restrictions(self):
        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.store, server.token, server.exports = self.store, "unit-test-token", set()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method, path, headers=None, body=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            result = (response.status, response.read(), dict(response.getheaders()))
            connection.close()
            return result
        try:
            self.assertEqual(request("GET", "/api/state")[0], 200)
            self.assertEqual(request("GET", "/api/state", {"Host": "evil.test"})[0], 403)
            self.assertEqual(request("POST", "/api/export", body="{}")[0], 403)
            self.assertEqual(request("POST", "/api/export", {"X-Pairing-Token": server.token, "Origin": "https://evil.test"}, "{}")[0], 403)
            self.assertEqual(request("GET", "/../../etc/passwd")[0], 404)
            self.assertEqual(request("GET", "/api/photo/../../secret")[0], 400)
            self.assertEqual(request("POST", "/api/preview", {"X-Pairing-Token": server.token}, "{broken")[0], 400)
            status, _, headers = request("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
            self.assertNotIn("Access-Control-Allow-Origin", headers)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
