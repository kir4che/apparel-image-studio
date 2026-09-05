"""Build Windows portable version with PyInstaller."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "服飾圖片工作室"
BUILD = ROOT / "build"

def clean():
    for d in (DIST, BUILD):
        if d.exists():
            shutil.rmtree(d)

def build_exe():
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "服飾圖片工作室",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--add-data", f"{ROOT / 'studio-web'};studio-web",
        "--add-data", f"{ROOT / 'requirements.txt'};.",
        "--collect-all", "llama_cpp",
        "--hidden-import", "PIL",
        str(ROOT / "studio_server.py"),
    ]
    subprocess.run(cmd, check=True)

def copy_files():
    # Copy model directory
    models_src = ROOT / "models"
    models_dst = DIST / "models"
    if models_src.exists():
        shutil.copytree(models_src, models_dst)
    else:
        models_dst.mkdir(exist_ok=True)
        (models_dst / "README.txt").write_text(
            "Windows 內建 AI 必須同時放入以下兩個檔案：\n"
            "1. Qwen3.5-2B-Q4_K_S.gguf\n"
            "2. mmproj-F16.gguf（建議使用；其他相容的 mmproj-*.gguf 也可以）\n"
            "   這是讓模型能讀取照片的視覺投影檔。\n\n"
            "下載位置：https://huggingface.co/unsloth/Qwen3.5-2B-GGUF\n"
            "缺少視覺投影檔時，AI 裁切不會啟動。",
            encoding="utf-8"
        )

    # Create user directories
    (DIST / "使用者照片").mkdir(exist_ok=True)
    (DIST / "outputs").mkdir(exist_ok=True)

    # Create README
    readme = DIST / "使用說明.txt"
    readme.write_text(
        "服飾圖片工作室 - Windows 可攜版\n"
        "================================\n\n"
        "1. 將 Qwen3.5-2B-Q4_K_S.gguf 放入 models 資料夾\n"
        "2. 將 mmproj-F16.gguf 放入同一個 models 資料夾（其他相容的 mmproj-*.gguf 也可以）\n"
        "3. 雙擊「服飾圖片工作室.exe」\n"
        "4. 瀏覽器會自動開啟操作介面\n"
        "5. 照片請放入「使用者照片」資料夾\n"
        "6. 匯出的圖片會在「outputs」資料夾\n\n"
        "注意：首次啟動可能需要較長時間載入模型\n",
        encoding="utf-8"
    )

def main():
    print("清理舊的建置...")
    clean()
    print("建置 .exe...")
    build_exe()
    print("複製附加檔案...")
    copy_files()
    print(f"建置完成：{DIST}")

if __name__ == "__main__":
    main()
