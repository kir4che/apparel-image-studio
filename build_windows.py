"""Build Windows portable version with PyInstaller."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

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
        "--windowed",
        "--add-data", f"{ROOT / 'work' / 'studio-web'};studio-web",
        "--add-data", f"{ROOT / 'work' / 'requirements.txt'};.",
        "--hidden-import", "llama_cpp",
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
            "請將 Qwen3.5-2B 的 .gguf 模型檔放入此資料夾\n"
            "下載位置：https://huggingface.co/unsloth/Qwen3.5-2B-GGUF\n"
            "建議使用 Q4_K_S 量化版本",
            encoding="utf-8"
        )

    # Create user directories
    (DIST / "使用者照片").mkdir(exist_ok=True)
    (DIST / "outputs").mkdir(exist_ok=True)

    # Create launcher batch file
    launcher = DIST / "啟動服飾圖片工作室.bat"
    launcher.write_text(
        '@echo off\n'
        'cd /d "%~dp0"\n'
        'start "" "服飾圖片工作室.exe"\n',
        encoding="utf-8"
    )

    # Create README
    readme = DIST / "使用說明.txt"
    readme.write_text(
        "服飾圖片工作室 - Windows 可攜版\n"
        "================================\n\n"
        "1. 將 Qwen3.5-2B 的 .gguf 模型檔放入 models 資料夾\n"
        "2. 雙擊「啟動服飾圖片工作室.bat」或「服飾圖片工作室.exe」\n"
        "3. 瀏覽器會自動開啟操作介面\n"
        "4. 照片請放入「使用者照片」資料夾\n"
        "5. 匯出的圖片會在「outputs」資料夾\n\n"
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
