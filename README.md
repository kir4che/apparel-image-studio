# 服飾圖片工作室

本機服飾商品圖工作台，用來把同一件商品的一批照片整理成可匯出的商品圖。

它支援拼圖、單張裁切、刪除照片、AI 批次裁切上半臉，以及一次匯出所有圖片。工具只在 `127.0.0.1` 執行，不會覆寫原始檔。

## 功能

- 拼圖：選兩張照片，依勾選順序組成左右拼圖
- 單張裁切：針對某張照片調整裁切範圍
- 刪除照片：用獨立模式集中刪除，降低誤觸
- AI 批次裁切：偵測完整頭部，只移除上半臉上方的橫帶
- 匯出所有圖片：輸出未配對照片、裁切後照片與完成的拼圖
- 本機自動保存：保留目前商品、拼圖與裁切狀態

## 快速開始

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python studio_server.py --open
```

啟動後會開啟：

```text
http://127.0.0.1:8765/
```

## 使用流程

1. 匯入同一件商品的照片。
2. 預設在「拼圖」模式，勾選兩張照片後右側會顯示預覽。
3. 需要時切到「單張裁切」或「刪除照片」。
4. 需要 AI 裁頭時，先啟動本機 AI，再按「分析照片」。
5. 完成後按「匯出所有圖片」，結果會放在 `outputs/` 的新資料夾。

## AI 裁切設定

一般拼圖和手動裁切不需要 AI。只有使用「AI 批次裁切」時，才需要本機模型。

Mac 開發測試可使用 LM Studio，並啟動本機 Server：`http://127.0.0.1:1234`。

Windows 可攜版若使用內建 AI，`models/` 必須同時放入：

```text
models/
  Qwen3.5-2B-Q4_K_S.gguf
  mmproj-F16.gguf
```

`mmproj-F16.gguf` 是讓模型讀取照片的視覺投影檔。缺少它時，AI 只能讀文字，裁切分析會失敗或沒有結果。

模型來源：[unsloth/Qwen3.5-2B-GGUF](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF)

> [!IMPORTANT]
> AI 裁切只是輔助。上架前仍應人工確認裁切線沒有切到服裝主體。

## 匯出規則

- 每次匯出都建立新的 `outputs/` 子資料夾
- 匯出內容包含未配對照片、裁切後照片與完成的拼圖
- 已用於拼圖的原照片不會重複單獨匯出

支援匯入 JPG/JPEG、PNG、WebP、AVIF、BMP、TIFF 與靜態 GIF。不支援 HEIC/HEIF。

## 開發

常用檢查：

```bash
.venv/bin/python -m py_compile garment_pilot.py studio_server.py build_windows.py
node --check studio-web/app.js
.venv/bin/python -m unittest test_garment_pilot.py test_studio.py test_studio_changes.py
git diff --check
```

Windows 可攜版打包：

```bash
python build_windows.py
```

核心檔案：

```text
studio_server.py   本機服務與 API
studio_core.py     匯入、裁切、拼圖、匯出
garment_pilot.py   AI 裁切與模型連線
studio-web/        前端介面
build_windows.py   Windows 打包
```
