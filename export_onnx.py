"""把 SegFormer 服飾解析模型匯出成 ONNX，供可攜版在不裝 torch 的情況下使用。

只用於開發與打包（需要 torch、transformers），執行後產生
models/segformer_b3_clothes.onnx。可重複執行，會覆寫舊檔。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "models" / "segformer_b3_clothes.onnx"
MODEL_ID = "sayeed99/segformer_b3_clothes"
EDGE = 768


def preprocess(image, resample=Image.Resampling.BICUBIC):
    """與 studio_cutout 執行時相同的正規化，方便比對是否一致。"""
    img = image.convert("RGB").resize((EDGE, EDGE), resample)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = (arr - np.array([0.485, 0.456, 0.406], np.float32)) / np.array(
        [0.229, 0.224, 0.225], np.float32
    )
    return arr.transpose(2, 0, 1)[None]


def main():
    import torch
    from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

    processor = SegformerImageProcessor.from_pretrained(MODEL_ID)
    model = SegformerForSemanticSegmentation.from_pretrained(MODEL_ID).eval()

    # 確認手寫前處理與 transformers 一致（差 1e-3 內就當同一個管線）。
    sample = Image.new("RGB", (320, 480), (120, 160, 90))
    sample.putpixel((10, 10), (255, 0, 0))
    reference = processor(images=sample, size={"height": EDGE, "width": EDGE}, return_tensors="np")
    for name, resample in (("bicubic", Image.Resampling.BICUBIC), ("bilinear", Image.Resampling.BILINEAR)):
        diff = np.abs(reference["pixel_values"] - preprocess(sample, resample)).max()
        print(f"前處理 {name} 與 transformers 最大差異 {diff:.6f}")

    class Wrapper(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner

        def forward(self, pixel_values):
            return self.inner(pixel_values=pixel_values).logits

    OUT.parent.mkdir(exist_ok=True)
    dummy = torch.zeros(1, 3, EDGE, EDGE)
    torch.onnx.export(
        Wrapper(model).eval(), dummy, str(OUT),
        input_names=["pixel_values"], output_names=["logits"], opset_version=18,
        do_constant_folding=True,
    )
    # torch 會把權重寫成 .onnx.data 外掛檔，這裡併回單一檔案方便打包。
    import onnx

    merged = onnx.load(str(OUT))
    merged.ClearField("metadata_props")
    OUT.unlink()
    onnx.save(merged, str(OUT), save_as_external_data=False)
    (OUT.with_suffix(".onnx.data")).unlink(missing_ok=True)
    print(f"已匯出 {OUT} ({OUT.stat().st_size / 1048576:.0f} MB)")

    # 匯出後立刻用 onnxruntime 跑一次，確認類別分數與 PyTorch 相符。
    import onnxruntime as ort

    test = np.random.default_rng(0).random((1, 3, EDGE, EDGE), dtype=np.float32)
    with torch.inference_mode():
        torch_logits = model(pixel_values=torch.from_numpy(test)).logits.numpy()
    onnx_logits = ort.InferenceSession(str(OUT), providers=["CPUExecutionProvider"]).run(
        None, {"pixel_values": test}
    )[0]
    print("ONNX 與 PyTorch logits 最大差異", float(np.abs(torch_logits - onnx_logits).max()))


if __name__ == "__main__":
    sys.exit(main())
