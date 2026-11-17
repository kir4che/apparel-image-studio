"""服飾照去背：人物與衣物都保留、只去除背景，輸出透明 PNG。需要 onnxruntime 與模型檔。"""
from __future__ import annotations

from pathlib import Path
import re
import tempfile
import threading
import time

import numpy as np
from PIL import Image, ImageFilter

from garment_pilot import MODEL_DIR, read_image

MODEL_FILENAME = "segformer_b3_clothes.onnx"
EDGE = 768          # 推論解析度，太小會漏掉衣角與鈕扣
MASK_EDGE = 2400    # 遮罩長邊上限：4000px 以上的大圖插值 18 類 logits 會吃掉大量記憶體
MAX_EDGE = 2400     # 輸出長邊上限，避免 4000px 以上的透明 PNG 佔空間
MARGIN = 0.04       # 四周留白比例，讓同批圖片尺寸一致
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_session = None
_session_lock = threading.Lock()
_ort = None


def available():
    """不載入模型，只確認套件能 import（打包成 exe 後 find_spec 不一定看得到）。"""
    global _ort
    if _ort is None:
        try:
            import onnxruntime

            _ort = onnxruntime
        except ImportError:
            _ort = False
    return _ort is not False


def downloaded():
    """模型檔是否已就位。可攜版隨附在 models/，開發時用 export_onnx.py 產生。"""
    return (MODEL_DIR / MODEL_FILENAME).is_file()


def _load():
    global _session
    with _session_lock:
        if _session is None:
            import onnxruntime as ort

            _session = ort.InferenceSession(str(MODEL_DIR / MODEL_FILENAME), providers=["CPUExecutionProvider"])
        return _session


def _pixel_values(image):
    """與 transformers 處理器一致：縮到 768 用 bilinear，換成 bicubic 會差 0.5。"""
    img = image.convert("RGB").resize((EDGE, EDGE), Image.Resampling.BILINEAR)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    return ((arr - MEAN) / STD).transpose(2, 0, 1)[None]


def subject_mask(image, session):
    """人體解析模型把人物、衣物、配件標成非 0 類別、背景是 0，保留非背景再去背。

    真人穿搭照會留下人與衣服，平拍衣服照會留下衣服，同一條規則都適用。
    先在長邊 MASK_EDGE 內插值並 argmax，再把類別遮罩放大回原尺寸，
    避免 18 類 logits 插值到 4000px 以上。
    """
    logits = session.run(None, {"pixel_values": _pixel_values(image)})[0][0]
    scale = min(1.0, MASK_EDGE / max(image.size))
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    # 逐類別放大並即時比較，避免一次佔用 18 張 float 圖層。
    best = labels = None
    for index, logit in enumerate(logits):
        up = np.asarray(Image.fromarray(logit).resize(size, Image.Resampling.BILINEAR))
        if best is None:
            best, labels = up.copy(), np.zeros(up.shape, dtype="uint8")
            continue
        better = up > best
        labels[better] = index
        best[better] = up[better]
    if size != image.size:
        labels = np.asarray(Image.fromarray(labels).resize(image.size, Image.Resampling.NEAREST))
    return labels != 0


def _alpha(mask):
    alpha = Image.fromarray((np.asarray(mask, dtype=bool) * 255).astype("uint8"))
    return alpha.filter(ImageFilter.GaussianBlur(1))


def transparent_cutout(image, mask):
    """原尺寸的透明版本，用來覆蓋照片庫原圖，讓裁切與配對的座標維持有效。"""
    rgba = image.convert("RGBA")
    rgba.putalpha(_alpha(mask))
    return rgba


def finish_cutout(image, mask):
    """把遮罩變成置中、四邊留白的透明 PNG。純 Pillow，方便單獨測試。"""
    alpha = _alpha(mask)
    rgba = image.convert("RGBA")
    rgba.putalpha(alpha)
    box = alpha.getbbox()
    if box is None:
        raise ValueError("這張照片沒有偵測到可保留的主體")
    rgba = rgba.crop(box)
    side = round(max(rgba.size) * (1 + 2 * MARGIN))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(rgba, ((side - rgba.width) // 2, (side - rgba.height) // 2))
    if side > MAX_EDGE:
        canvas = canvas.resize((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    return canvas


def cutout_photos(store, photo_ids):
    if not available():
        raise ValueError(
            "尚未安裝去背引擎，請在工具資料夾執行 "
            ".venv/bin/python -m pip install onnxruntime，再重新啟動工具"
        )
    if not downloaded():
        raise ValueError(
            f"找不到去背模型 {MODEL_FILENAME}，請確認 models 資料夾（開發時可執行 export_onnx.py 產生）"
        )
    session = _load()
    run = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-") + "去背-", dir=store.output))
    results, skipped = [], []
    for photo_id in photo_ids:
        photo = store.photo(photo_id)
        image = read_image(store.folder / f"{photo_id}.png", formats=("PNG",))
        try:
            mask = subject_mask(image, session)
            cutout = finish_cutout(image, mask)
        except ValueError:
            skipped.append(photo["name"])
            continue
        stem = re.sub(r"[^\w\-.]+", "_", Path(photo["name"]).stem)[:40] or "照片"
        path = run / f"{len(results) + 1:03d}-{stem}-去背.png"
        cutout.save(path, optimize=True)
        results.append({"id": photo_id, "name": photo["name"], "file": path.name,
                        "bytes": path.stat().st_size, "size": list(cutout.size)})
        store.replace_photo_image(photo_id, transparent_cutout(image, mask))
    if not results:
        run.rmdir()
        raise ValueError("這些照片都沒有偵測到可保留的主體，請換一組照片")
    return {"batch": run.name, "folder": str(run), "count": len(results),
            "skipped": skipped, "results": results}
