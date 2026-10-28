import asyncio
import os
import socket
import time
from concurrent.futures import ProcessPoolExecutor
from io import BytesIO

from fastapi import FastAPI, HTTPException, Request
from PIL import Image, ImageFilter


# Konfigurasi worker
CORES = int(os.getenv("WORKER_CORES", os.cpu_count() or 1))
VM_ID = os.getenv("VM_ID", socket.gethostname())
PASSES = 2

app = FastAPI()

# Menjalankan pemrosesan gambar menggunakan proses anak.
pool = ProcessPoolExecutor(max_workers=CORES)


def process_image(data: bytes, scale: float, passes: int):
    """
    Memproses gambar sebanyak passes kali.
    Mengembalikan byte gambar, status validasi, dan waktu eksekusi.
    """
    start_time = time.perf_counter()
    current_data = data

    for _ in range(passes):
        # Membuka gambar dan mengubahnya menjadi RGB.
        with Image.open(BytesIO(current_data)) as source:
            image = source.convert("RGB")

        # Gaussian Blur.
        image = image.filter(
            ImageFilter.GaussianBlur(radius=2)
        )

        # Resize menggunakan LANCZOS.
        width, height = image.size
        new_width = max(1, int(width * scale))
        new_height = max(1, int(height * scale))

        image = image.resize(
            (new_width, new_height),
            Image.Resampling.LANCZOS,
        )

        # Sharpen menggunakan Unsharp Mask.
        image = image.filter(
            ImageFilter.UnsharpMask()
        )

        # Menyimpan hasil ke memori dalam format JPEG.
        output_buffer = BytesIO()
        image.save(
            output_buffer,
            format="JPEG",
            quality=85,
            optimize=True,
        )

        current_data = output_buffer.getvalue()

    # Memeriksa apakah gambar hasil dapat dibuka dan diverifikasi.
    try:
        with Image.open(BytesIO(current_data)) as result:
            result.verify()
        ok = True
    except Exception:
        ok = False

    exec_s = time.perf_counter() - start_time

    return current_data, ok, exec_s


@app.get("/health")
async def health():
    """Memeriksa apakah worker tersedia."""
    return {
        "status": "ok",
        "vm_id": VM_ID,
        "cores": CORES,
    }


@app.post("/process")
async def process(
    request: Request,
    task_id: int,
    target_scale: float = 0.5,
):
    """Menerima dan memproses satu gambar."""
    data = await request.body()

    # Request tanpa gambar harus ditolak.
    if not data:
        raise HTTPException(
            status_code=400,
            detail="Request body kosong",
        )

    # Skala harus positif agar ukuran gambar valid.
    if target_scale <= 0:
        raise HTTPException(
            status_code=400,
            detail="target_scale harus lebih besar dari 0",
        )

    loop = asyncio.get_running_loop()

    try:
        output_data, ok, exec_s = await loop.run_in_executor(
            pool,
            process_image,
            data,
            target_scale,
            PASSES,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Gambar tidak valid atau gagal diproses",
        ) from exc

    # out_bytes adalah ukuran output dalam byte,
    # bukan isi gambar yang dikirim kembali.
    return {
        "task_id": task_id,
        "vm_id": VM_ID,
        "exec_s": round(exec_s, 6),
        "out_bytes": len(output_data),
        "ok": ok,
    }