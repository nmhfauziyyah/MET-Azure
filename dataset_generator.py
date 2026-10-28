
import argparse
import csv
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


# Ukuran gambar dan persentasenya sesuai spesifikasi proyek.
IMAGE_SIZES = [
    (640, 480),
    (1280, 720),
    (1920, 1080),
    (2560, 1440),
]

SIZE_RATIOS = [0.30, 0.30, 0.30, 0.10]


def make_size_plan(count, rng):
    """Menyusun ukuran gambar sesuai persentase yang ditentukan."""
    exact_counts = [count * ratio for ratio in SIZE_RATIOS]
    size_counts = [int(value) for value in exact_counts]

    # Membagikan sisa gambar berdasarkan pecahan terbesar.
    remainder = count - sum(size_counts)
    order = sorted(
        range(len(IMAGE_SIZES)),
        key=lambda i: exact_counts[i] - size_counts[i],
        reverse=True,
    )

    for i in order[:remainder]:
        size_counts[i] += 1

    sizes = []
    for size, amount in zip(IMAGE_SIZES, size_counts):
        sizes.extend([size] * amount)

    rng.shuffle(sizes)
    return sizes


def make_image(width, height, rng):
    """Membuat gambar gradien, bentuk acak, dan noise."""
    # Membuat gradien warna RGB.
    x_gradient = np.linspace(
        0, 255, width, dtype=np.uint8
    )
    y_gradient = np.linspace(
        0, 255, height, dtype=np.uint8
    )

    image_array = np.empty(
        (height, width, 3), dtype=np.uint8
    )

    image_array[:, :, 0] = x_gradient[None, :]
    image_array[:, :, 1] = y_gradient[:, None]

    blue = (
        x_gradient[None, :].astype(np.uint16)
        + y_gradient[:, None].astype(np.uint16)
    ) // 2

    image_array[:, :, 2] = blue.astype(np.uint8)

    image = Image.fromarray(image_array)
    draw = ImageDraw.Draw(image)

    # Menggambar beberapa bentuk dengan posisi dan warna acak.
    for _ in range(6):
        x1, x2 = sorted(
            rng.integers(0, width, size=2).tolist()
        )
        y1, y2 = sorted(
            rng.integers(0, height, size=2).tolist()
        )

        color = tuple(
            int(value)
            for value in rng.integers(0, 256, size=3)
        )

        shape_type = int(rng.integers(0, 3))

        if shape_type == 0:
            draw.rectangle((x1, y1, x2, y2), fill=color)
        elif shape_type == 1:
            draw.ellipse((x1, y1, x2, y2), fill=color)
        else:
            draw.line(
                (x1, y1, x2, y2),
                fill=color,
                width=int(rng.integers(1, 8)),
            )

    # Menambahkan noise acak pada gambar.
    pixels = np.asarray(image, dtype=np.int16)
    noise = rng.integers(
        -18, 19, size=pixels.shape, dtype=np.int16
    )

    noisy_pixels = np.clip(
        pixels + noise, 0, 255
    ).astype(np.uint8)

    return Image.fromarray(noisy_pixels)


def generate_dataset(count, out_dir, seed):
    """Membuat gambar JPEG dan manifest.csv."""
    if count <= 0:
        raise ValueError("Jumlah gambar harus lebih dari 0.")

    rng = np.random.default_rng(seed)
    out_dir = Path(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)

    # Untuk data/images, manifest disimpan di data/manifest.csv.
    manifest_path = out_dir.parent / "manifest.csv"

    size_plan = make_size_plan(count, rng)
    records = []
    total_bytes = 0
    total_length = 0.0

    for task_id, (width, height) in enumerate(
        size_plan, start=1
    ):
        filename = f"img_{task_id:04d}.jpg"
        image_path = out_dir / filename

        image = make_image(width, height, rng)

        # Simpan gambar dalam format JPEG dengan kualitas 92.
        image.save(
            image_path,
            format="JPEG",
            quality=92,
        )

        size_bytes = image_path.stat().st_size
        length = width * height / 10000

        records.append({
            "task_id": task_id,
            "filename": filename,
            "width": width,
            "height": height,
            "size_bytes": size_bytes,
            "length": f"{length:.2f}",
        })

        total_bytes += size_bytes
        total_length += length

    # Menulis manifest dengan nama kolom sesuai kontrak proyek.
    columns = [
        "task_id",
        "filename",
        "width",
        "height",
        "size_bytes",
        "length",
    ]

    with manifest_path.open(
        "w", newline="", encoding="utf-8"
    ) as csv_file:
        writer = csv.DictWriter(
            csv_file, fieldnames=columns
        )
        writer.writeheader()
        writer.writerows(records)

    # Menampilkan ringkasan dataset.
    print("\n=== DATASET BERHASIL DIBUAT ===")
    print(f"Jumlah gambar : {count}")

    for width, height in IMAGE_SIZES:
        amount = sum(
            1 for record in records
            if record["width"] == width
            and record["height"] == height
        )
        print(f"{width}x{height}: {amount} gambar")

    print(f"Total ukuran file : {total_bytes:,} byte")
    print(f"Total length      : {total_length:.2f}")
    print(f"Folder gambar     : {out_dir}")
    print(f"Manifest          : {manifest_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Generator dataset gambar untuk proyek MET Azure."
    )

    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Jumlah gambar yang akan dibuat (default: 100)",
    )

    parser.add_argument(
        "--out",
        type=str,
        default="data/images",
        help="Folder penyimpanan gambar",
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed agar pembuatan dataset dapat diulang",
    )

    args = parser.parse_args()

    generate_dataset(
        count=args.count,
        out_dir=args.out,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
