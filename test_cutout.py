import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from studio_cutout import available, finish_cutout, subject_mask, transparent_cutout


class FinishCutoutTest(unittest.TestCase):
    def test_trims_and_pads_to_square(self):
        image = Image.new("RGB", (200, 300), "white")
        mask = np.zeros((300, 200), dtype=bool)
        mask[50:250, 60:140] = True
        out = finish_cutout(image, mask)
        self.assertEqual(out.mode, "RGBA")
        self.assertEqual(out.width, out.height)
        self.assertGreater(out.width, 200)
        self.assertEqual(out.getchannel("A").getpixel((0, 0)), 0)
        box = out.getchannel("A").getbbox()
        self.assertGreater(box[2] - box[0], 70)
        self.assertGreater(box[3] - box[1], 190)

    def test_empty_mask_rejected(self):
        with self.assertRaises(ValueError):
            finish_cutout(Image.new("RGB", (20, 20)), np.zeros((20, 20), dtype=bool))

    def test_transparent_cutout_keeps_size(self):
        image = Image.new("RGB", (30, 20), "red")
        mask = np.zeros((20, 30), dtype=bool)
        mask[5:15, 5:25] = True
        out = transparent_cutout(image, mask)
        self.assertEqual(out.size, (30, 20))
        self.assertEqual(out.getpixel((0, 0))[3], 0)
        self.assertEqual(out.getpixel((10, 10))[3], 255)


class SubjectMaskTest(unittest.TestCase):
    """大圖也要回原尺寸遮罩，且不在原尺寸插值 18 類 logits。"""

    @unittest.skipUnless(available(), "需要 torch 與 transformers")
    def test_large_image_mask_matches_source_size(self):
        import torch

        class Processor:
            def __call__(self, images, size, return_tensors):
                return {}

        class Model:
            def __call__(self, **inputs):
                logits = torch.zeros(1, 18, 192, 192)
                logits[:, 4, 60:130, 70:120] = 5  # 上衣
                return type("Out", (), {"logits": logits})()

        image = Image.new("RGB", (4000, 2400), "white")
        mask = subject_mask(image, Processor(), Model())
        self.assertEqual(mask.shape, (2400, 4000))
        self.assertEqual(mask.dtype, np.bool_)
        self.assertTrue(mask[1200, 2000])
        self.assertFalse(mask[0, 0])


class CutoutPhotosTest(unittest.TestCase):
    """批次流程：跳過失敗的照片、編號不跳號、原圖被透明版覆蓋。"""

    def _store(self, root, sizes):
        store = type("Store", (), {})()
        store.output = root / "outputs"
        store.output.mkdir()
        store.folder = root / "data"
        store.folder.mkdir()
        store.names, store.replaced = {}, {}
        for photo_id, size in sizes.items():
            Image.new("RGB", size, "white").save(store.folder / f"{photo_id}.png")
            store.names[photo_id] = f"{photo_id}.png"
        store.photo = lambda photo_id: {"id": photo_id, "name": store.names[photo_id]}

        def replace(photo_id, image):
            store.replaced[photo_id] = image.size

        store.replace_photo_image = replace
        return store

    def test_skipped_photo_does_not_leave_gap(self):
        import studio_cutout

        def mask(image, processor, model):
            found = np.zeros((image.height, image.width), dtype=bool)
            if image.size == (40, 40):  # 10×10 的那張當成找不到主體
                found[10:30, 10:30] = True
            return found

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp), {"a": (40, 40), "b": (10, 10), "c": (40, 40)})
            with mock.patch.multiple(studio_cutout, available=lambda: True,
                                     _load=lambda: (None, None), subject_mask=mask):
                result = studio_cutout.cutout_photos(store, ["a", "b", "c"])
            names = sorted(path.name for path in Path(result["folder"]).iterdir())
            self.assertEqual(names, ["001-a-去背.png", "002-c-去背.png"])
            self.assertEqual(result["skipped"], ["b.png"])
            self.assertEqual(len(result["results"]), 2)
            self.assertEqual(sorted(store.replaced), ["a", "c"])
            self.assertEqual(store.replaced["a"], (40, 40))

    def test_all_skipped_removes_empty_folder(self):
        import studio_cutout

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(Path(tmp), {"a": (10, 10)})
            with mock.patch.multiple(studio_cutout, available=lambda: True,
                                     _load=lambda: (None, None),
                                     subject_mask=lambda image, p, m: np.zeros((image.height, image.width), dtype=bool)):
                with self.assertRaises(ValueError):
                    studio_cutout.cutout_photos(store, ["a"])
            self.assertEqual(list(store.output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
