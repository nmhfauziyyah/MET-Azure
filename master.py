import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
import json
import os
import queue
import sys
import threading
import time

from PIL import Image
import requests

from scheduler import compute_metrics, explain_met, met, rr


CALIB_WIDTH = 1920
CALIB_HEIGHT = 1080
CALIB_LENGTH = (CALIB_WIDTH * CALIB_HEIGHT) / 10000.0  # 207.36


def create_calibration_image() -> bytes:
    """
    Membuat 1 gambar kalibrasi 1920x1080 di memori (length 207.36).
    Tidak menyimpan file ke disk.
    """
    buffer = BytesIO()
    image = Image.new("RGB", (CALIB_WIDTH, CALIB_HEIGHT), color=(128, 128, 128))
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def load_manifest(manifest_path: str, limit: int | None = None) -> list[dict]:
    """
    Membaca manifest.csv dan mengambil limit baris pertama.
    Hanya membaca metadata teks ke memori; file gambar tidak dibaca di sini.
    """
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f"File manifest tidak ditemukan: {manifest_path}")

    tasks = []
    with open(manifest_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tasks.append({
                "task_id": int(row["task_id"]),
                "filename": row["filename"],
                "length": float(row["length"]),
            })
            if limit is not None and len(tasks) >= limit:
                break

    return tasks


def load_workers(config_path: str) -> list[dict]:
    """
    Membaca file konfigurasi workers (JSON).
    """
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"File konfigurasi worker tidak ditemukan: {config_path}")

    with open(config_path, mode="r", encoding="utf-8") as f:
        data = json.load(f)

    return data


def check_worker_health(workers: list[dict], timeout: float = 3.0) -> tuple[list[str], dict[str, int], dict[str, str]]:
    """
    Langkah 2: GET /health tiap worker (timeout 3 detik).
    Mengembalikan (healthy_list, cores_dict, urls_dict).
    """
    print("\n" + "=" * 65)
    print("PEMERIKSAAN KESEHATAN WORKER (GET /health)")
    print("=" * 65)

    healthy = []
    cores = {}
    urls = {}

    for w in workers:
        vm_id = w["vm_id"]
        url = w["url"].rstrip("/")
        urls[vm_id] = url

        try:
            resp = requests.get(f"{url}/health", timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "ok":
                    c = int(data.get("cores", 1))
                    cores[vm_id] = c
                    healthy.append(vm_id)
                    print(f"[{vm_id}] URL: {url:26} -> STATUS: SEHAT (cores: {c})")
                    continue
            print(f"[{vm_id}] URL: {url:26} -> STATUS: TIDAK SEHAT (HTTP {resp.status_code})")
        except Exception as exc:
            print(f"[{vm_id}] URL: {url:26} -> STATUS: TIDAK SEHAT ({exc.__class__.__name__})")

    print("-" * 65)
    print(f"Total Worker Sehat: {len(healthy)} / {len(workers)}")
    print("=" * 65)

    if not healthy:
        raise RuntimeError("Semua worker tidak sehat atau tidak dapat dihubungi!")

    return healthy, cores, urls


def calibrate_workers(healthy: list[str], cores: dict[str, int], urls: dict[str, str]) -> dict[str, float]:
    """
    Langkah 3: Kalibrasi kapasitas worker.
    Kirim paralel `cores` request /process berisi gambar kalibrasi 1920x1080 (length 207.36).
    T = waktu dinding sampai semua request selesai.
    capacity = cores * 207.36 / T.
    """
    print("\n" + "=" * 65)
    print("KALIBRASI KAPASITAS WORKER (Length: 207.36)")
    print("=" * 65)

    calib_bytes = create_calibration_image()
    capacity = {}

    for vm_id in list(healthy):
        c = cores[vm_id]
        url = urls[vm_id]

        def _send_calib(task_id: int):
            resp = requests.post(
                f"{url}/process",
                params={"task_id": task_id, "target_scale": 0.5},
                data=calib_bytes,
                headers={"Content-Type": "application/octet-stream"},
                timeout=30,
            )
            resp.raise_for_status()
            res_json = resp.json()
            if not res_json.get("ok"):
                raise RuntimeError(f"Worker {vm_id} menghasilkan ok=False")
            return res_json

        t_start = time.perf_counter()
        try:
            with ThreadPoolExecutor(max_workers=c) as executor:
                futures = [executor.submit(_send_calib, i + 1) for i in range(c)]
                for f in futures:
                    f.result()
            t_wall = time.perf_counter() - t_start
            t_wall = max(t_wall, 0.0001)

            cap = (c * CALIB_LENGTH) / t_wall
            capacity[vm_id] = round(cap, 4)
            print(
                f"[{vm_id}] Cores: {c} | Waktu Dinding (T): {t_wall:.3f} s "
                f"-> Kapasitas: {capacity[vm_id]:.2f} length/s"
            )
        except Exception as exc:
            print(f"[{vm_id}] Gagal saat kalibrasi ({exc}). Ditandai tidak sehat.")
            healthy.remove(vm_id)

    print("=" * 65)
    if not healthy:
        raise RuntimeError("Semua worker gagal saat proses kalibrasi!")

    return capacity


def execute_tasks(
    algo: str,
    tasks: list[dict],
    images_dir: str,
    healthy: list[str],
    cores: dict[str, int],
    urls: dict[str, str],
    capacity: dict[str, float],
) -> tuple[list[dict], float]:
    """
    Langkah 6: Eksekusi task menggunakan ThreadPoolExecutor per worker.
    Pencatatan send_s, finish_s, exec_s dan penanganan failover dinamis.
    """
    active_healthy = list(healthy)
    task_lengths = [t["length"] for t in tasks]

    # Penjadwalan awal
    if algo == "MET":
        initial_assignments = met(task_lengths, capacity, active_healthy)
    else:  # RR
        initial_assignments = rr(len(tasks), active_healthy)

    # Inisialisasi antrean task per worker
    worker_queues: dict[str, queue.Queue] = {vm: queue.Queue() for vm in active_healthy}
    for t, vm_id in zip(tasks, initial_assignments):
        worker_queues[vm_id].put(dict(t))

    records: list[dict] = []
    records_lock = threading.Lock()
    failover_lock = threading.Lock()
    stop_event = threading.Event()
    completed_count = 0
    total_tasks = len(tasks)

    t0 = time.perf_counter()

    def worker_loop(vm_id: str):
        nonlocal completed_count
        while not stop_event.is_set():
            # Jika worker sudah tidak sehat dan antreannya habis, keluar
            if vm_id not in active_healthy and worker_queues[vm_id].empty():
                break

            try:
                task = worker_queues[vm_id].get(timeout=0.1)
            except queue.Empty:
                if stop_event.is_set():
                    break
                continue

            # Baca file gambar dari disk saat akan dikirim (hemat memori)
            image_path = os.path.join(images_dir, task["filename"])
            try:
                with open(image_path, "rb") as img_file:
                    image_bytes = img_file.read()
            except Exception as e:
                print(f"\n[ERROR] Gagal membaca gambar {image_path}: {e}")
                # Jika file corrupt / tidak terbaca, jangan loop gagal
                continue

            send_s = time.perf_counter() - t0

            # Kirim request ke worker
            success = False
            try:
                resp = requests.post(
                    f"{urls[vm_id]}/process",
                    params={"task_id": task["task_id"], "target_scale": 0.5},
                    data=image_bytes,
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        finish_s = time.perf_counter() - t0
                        exec_s = float(data.get("exec_s", 0.0))
                        success = True

                        record = {
                            "task_id": task["task_id"],
                            "filename": task["filename"],
                            "length": task["length"],
                            "vm_id": vm_id,
                            "send_s": round(send_s, 6),
                            "finish_s": round(finish_s, 6),
                            "exec_s": round(exec_s, 6),
                        }
                        with records_lock:
                            records.append(record)
                            completed_count += 1
                            if completed_count >= total_tasks:
                                stop_event.set()
            except Exception as exc:
                print(f"\n[FAILOVER] Request task {task['task_id']} ke {vm_id} gagal: {exc}")

            if not success:
                # Penanganan Failover: tandai VM tidak sehat dan jadwalkan ulang task
                with failover_lock:
                    if vm_id in active_healthy:
                        active_healthy.remove(vm_id)
                        print(f"[FAILOVER] Worker {vm_id} ditandai TIDAK SEHAT. Sisa worker: {active_healthy}")

                    if not active_healthy:
                        print("[FATAL] Semua worker gagal, tidak ada worker tersisa!")
                        stop_event.set()
                        break

                    # Ambil task yang gagal dan seluruh task yang masih antre di worker ini
                    tasks_to_reschedule = [task]
                    while True:
                        try:
                            t_drain = worker_queues[vm_id].get_nowait()
                            tasks_to_reschedule.append(t_drain)
                        except queue.Empty:
                            break

                    print(f"[FAILOVER] Menjadwalkan ulang {len(tasks_to_reschedule)} task dari {vm_id}...")
                    rem_lengths = [t["length"] for t in tasks_to_reschedule]
                    if algo == "MET":
                        new_targets = met(rem_lengths, capacity, active_healthy)
                    else:
                        new_targets = rr(len(tasks_to_reschedule), active_healthy)

                    for t_item, target_vm in zip(tasks_to_reschedule, new_targets):
                        worker_queues[target_vm].put(t_item)

    # Jalankan satu ThreadPoolExecutor per worker dengan max_workers = cores
    executors = {}
    for vm_id in healthy:
        num_workers = cores[vm_id]
        pool = ThreadPoolExecutor(max_workers=num_workers)
        for _ in range(num_workers):
            pool.submit(worker_loop, vm_id)
        executors[vm_id] = pool

    # Tunggu semua task selesai
    while not stop_event.is_set():
        time.sleep(0.05)

    makespan = time.perf_counter() - t0

    for pool in executors.values():
        pool.shutdown(wait=True)

    records.sort(key=lambda r: r["task_id"])
    return records, makespan


def run_pipeline(
    algo: str,
    tasks: list[dict],
    workers_config: list[dict],
    images_dir: str,
    label: str,
    out_dir: str,
):
    """
    Menjalankan alur lengkap (Langkah 2 - Langkah 8) untuk satu algoritma.
    """
    print("\n" + "=" * 65)
    print(f"MEMULAI PIPELINE: {algo} (Label: {label}, Task Count: {len(tasks)})")
    print("=" * 65)

    # 2. Health check
    healthy, cores, urls = check_worker_health(workers_config)
    initial_cores = dict(cores)

    # 3. Kalibrasi kapasitas
    capacity = calibrate_workers(healthy, cores, urls)

    # 4. Jadwal & Diagnostik
    task_lengths = [t["length"] for t in tasks]
    print("\n" + "=" * 65)
    print(f"PENJADWALAN & DIAGNOSTIK ({algo})")
    print("=" * 65)
    print(explain_met(task_lengths, capacity, healthy, n=min(3, len(tasks))))

    # 5. Estimasi waktu
    total_length = sum(task_lengths)
    max_cap = max(capacity.values()) if capacity else 1.0
    est_makespan = total_length / max_cap

    print("\n" + "=" * 65)
    print("ESTIMASI WAKTU EKSEKUSI")
    print("=" * 65)
    print(f"Total Beban (Length) : {total_length:.2f}")
    print(f"Kapasitas Maksimum   : {max_cap:.2f} length/s")
    print(f"Estimasi Makespan    : {est_makespan:.2f} detik")
    if est_makespan < 30.0 or est_makespan > 180.0:
        print(
            f"[PERINGATAN] Estimasi waktu ({est_makespan:.2f}s) di luar rentang 30-180 detik. "
            f"(Sesuaikan --limit atau PASSES pada worker jika perlu)."
        )
    print("=" * 65)

    # 6. Eksekusi
    print(f"\n[EKSEKUSI] Mengirim {len(tasks)} task ke worker sehat...")
    records, makespan = execute_tasks(
        algo=algo,
        tasks=tasks,
        images_dir=images_dir,
        healthy=healthy,
        cores=cores,
        urls=urls,
        capacity=capacity,
    )
    print(f"[EKSEKUSI] Selesai. Total task selesai: {len(records)}, Makespan: {makespan:.3f} s")

    # 7. Hitung metrik
    metrics = compute_metrics(records, initial_cores, makespan)

    # Cetak tabel per VM dan metrik ringkas
    print("\n" + "=" * 65)
    print(f"HASIL METRIK - ALGORITMA: {algo} (Label: {label})")
    print("=" * 65)
    print(f"{'VM ID':<15} {'Tasks':<10} {'Total Exec (s)':<18} {'Utilisasi (%)':<15} {'Kapasitas':<12}")
    print("-" * 65)

    # Hitung total exec per vm untuk tampilan
    exec_per_vm = {vm: 0.0 for vm in initial_cores}
    for r in records:
        exec_per_vm[r["vm_id"]] += r["exec_s"]

    for vm in initial_cores:
        t_count = metrics["tasks_per_vm"].get(vm, 0)
        t_exec = exec_per_vm.get(vm, 0.0)
        t_util = metrics["util_per_vm"].get(vm, 0.0)
        t_cap = capacity.get(vm, 0.0)
        print(f"{vm:<15} {t_count:<10} {t_exec:<18.3f} {t_util:<15.2f} {t_cap:<12.2f}")

    print("-" * 65)
    print(f"Total Tasks       : {metrics['n_tasks']}")
    print(f"Makespan          : {metrics['makespan_s']:.3f} s")
    print(f"Rata-rata Wait (s): {metrics['avg_wait_s']:.3f} s")
    print(f"Throughput        : {metrics['throughput']:.3f} task/s")
    print(f"Degree of Imbal.  : {metrics['DI']:.3f}")
    print(f"Rata-rata Util    : {metrics['avg_util_pct']:.2f} %")
    print("=" * 65)

    # 8. Simpan CSV
    os.makedirs(out_dir, exist_ok=True)
    timestamp_file = datetime.now().strftime("%Y%m%d_%H%M%S")
    timestamp_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # a. results/tasks_<algo>_<ts>.csv
    tasks_filename = f"tasks_{algo}_{timestamp_file}.csv"
    tasks_filepath = os.path.join(out_dir, tasks_filename)
    with open(tasks_filepath, mode="w", newline="", encoding="utf-8") as f:
        fieldnames = ["task_id", "filename", "length", "vm_id", "send_s", "finish_s", "exec_s"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow(r)
    print(f"\n[PENYIMPANAN] Task log disimpan ke: {tasks_filepath}")

    # b. results/summary.csv
    summary_filepath = os.path.join(out_dir, "summary.csv")
    summary_headers = [
        "timestamp",
        "label",
        "algo",
        "n_tasks",
        "makespan_s",
        "avg_wait_s",
        "throughput",
        "DI",
        "avg_util_pct",
        "tasks_per_vm",
        "util_per_vm",
        "capacity_per_vm",
    ]

    write_header = not os.path.exists(summary_filepath) or os.path.getsize(summary_filepath) == 0

    with open(summary_filepath, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=summary_headers)
        if write_header:
            writer.writeheader()

        writer.writerow({
            "timestamp": timestamp_iso,
            "label": label,
            "algo": algo,
            "n_tasks": metrics["n_tasks"],
            "makespan_s": round(metrics["makespan_s"], 4),
            "avg_wait_s": round(metrics["avg_wait_s"], 4),
            "throughput": round(metrics["throughput"], 4),
            "DI": round(metrics["DI"], 4),
            "avg_util_pct": round(metrics["avg_util_pct"], 2),
            "tasks_per_vm": json.dumps(metrics["tasks_per_vm"]),
            "util_per_vm": json.dumps({k: round(v, 2) for k, v in metrics["util_per_vm"].items()}),
            "capacity_per_vm": json.dumps({k: round(v, 2) for k, v in capacity.items()}),
        })
    print(f"[PENYIMPANAN] Summary ditambahkan ke: {summary_filepath}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Master node CLI untuk orkestrasi task pemrosesan gambar dengan MET dan Round Robin."
    )
    parser.add_argument(
        "--config",
        type=str,
        default="workers.json",
        help="Path ke file konfigurasi worker JSON (default: workers.json)",
    )
    parser.add_argument(
        "--manifest",
        type=str,
        default="data/manifest.csv",
        help="Path ke file manifest CSV (default: data/manifest.csv)",
    )
    parser.add_argument(
        "--images",
        type=str,
        default="data/images",
        help="Path ke direktori gambar (default: data/images)",
    )
    parser.add_argument(
        "--algo",
        type=str,
        choices=["MET", "RR", "BOTH"],
        default="MET",
        help="Algoritma penjadwalan: MET, RR, atau BOTH (default: MET)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Batasan jumlah task pertama yang diproses (default: semua task pada manifest)",
    )
    parser.add_argument(
        "--label",
        type=str,
        default="baseline",
        help="Label teks bebas untuk penanda run (contoh: baseline, setelah_resize, failover)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="results",
        help="Direktori keluaran untuk menyimpan CSV hasil (default: results)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # 1. Baca manifest dan filter batas task
    tasks = load_manifest(args.manifest, limit=args.limit)
    if not tasks:
        print("[ERROR] Tidak ada task yang ditemukan pada manifest.")
        sys.exit(1)

    print(f"[MASTER] Berhasil memuat {len(tasks)} task dari {args.manifest}")

    # Muat konfigurasi worker
    workers_config = load_workers(args.config)
    if not workers_config:
        print("[ERROR] File konfigurasi worker kosong.")
        sys.exit(1)

    # Tentukan urutan eksekusi algoritma
    if args.algo == "BOTH":
        algos_to_run = ["MET", "RR"]
    else:
        algos_to_run = [args.algo]

    for algo in algos_to_run:
        run_pipeline(
            algo=algo,
            tasks=tasks,
            workers_config=workers_config,
            images_dir=args.images,
            label=args.label,
            out_dir=args.out,
        )


if __name__ == "__main__":
    main()
