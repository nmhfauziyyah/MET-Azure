import argparse
import random
from statistics import mean


# =========================================================
# 1. ALGORITMA MET (MINIMUM EXECUTION TIME)
# =========================================================

def met(
    lengths: list[float],
    capacity: dict[str, float],
    healthy: list[str],
    tol: float = 0.05
) -> list[str]:
    """
    Menentukan worker tujuan setiap task menggunakan MET.

    lengths  : panjang/beban setiap task
    capacity : kapasitas setiap worker
    healthy  : daftar worker yang tersedia
    tol      : toleransi pemilihan worker, default 5%

    Return:
        List vm_id tujuan untuk setiap task.
    """

    if not healthy:
        raise ValueError("Tidak ada worker sehat.")

    if not 0 <= tol:
        raise ValueError("Toleransi tidak boleh negatif.")

    for vm_id in healthy:
        if vm_id not in capacity:
            raise ValueError(f"Kapasitas {vm_id} tidak tersedia.")

        if capacity[vm_id] <= 0:
            raise ValueError(f"Kapasitas {vm_id} harus positif.")

    assignments = []

    for length in lengths:
        if length < 0:
            raise ValueError("Length task tidak boleh negatif.")

        # Hitung estimasi waktu setiap worker.
        et = {
            vm_id: length / capacity[vm_id]
            for vm_id in healthy
        }

        # Cari estimasi waktu terkecil.
        best = min(et.values())

        # Pilih worker pertama yang masih berada
        # dalam toleransi terhadap ET minimum.
        threshold = best * (1 + tol)

        selected = next(
            vm_id
            for vm_id in healthy
            if et[vm_id] <= threshold
        )

        assignments.append(selected)

    return assignments


# =========================================================
# 2. ALGORITMA ROUND ROBIN
# =========================================================

def rr(n: int, healthy: list[str]) -> list[str]:
    """
    Membagi n task secara bergiliran ke worker sehat.
    """

    if n < 0:
        raise ValueError("Jumlah task tidak boleh negatif.")

    if n > 0 and not healthy:
        raise ValueError("Tidak ada worker sehat.")

    return [
        healthy[i % len(healthy)]
        for i in range(n)
    ]


# =========================================================
# 3. MENGHITUNG METRIK HASIL PENJADWALAN
# =========================================================

def compute_metrics(
    records: list[dict],
    cores: dict[str, int],
    makespan: float
) -> dict:
    """
    Menghitung metrik dari record task yang selesai.

    records:
        List dictionary yang berisi hasil setiap task.

    cores:
        Dictionary vm_id -> jumlah core.

    makespan:
        Durasi keseluruhan run dalam detik.
    """

    if makespan < 0:
        raise ValueError("Makespan tidak boleh negatif.")

    if any(core <= 0 for core in cores.values()):
        raise ValueError("Jumlah core setiap worker harus positif.")

    vm_ids = list(cores.keys())
    n = len(records)

    # Jumlah task per worker.
    tasks_per_vm = {
        vm_id: 0
        for vm_id in vm_ids
    }

    # Total waktu eksekusi yang dilaporkan worker.
    exec_per_vm = {
        vm_id: 0.0
        for vm_id in vm_ids
    }

    # T_j = finish_s terbesar pada setiap worker.
    finish_per_vm = {
        vm_id: 0.0
        for vm_id in vm_ids
    }

    for record in records:
        vm_id = record["vm_id"]

        if vm_id not in cores:
            raise ValueError(
                f"Worker {vm_id} tidak ditemukan di cores."
            )

        send_s = float(record["send_s"])
        finish_s = float(record["finish_s"])
        exec_s = float(record["exec_s"])

        if send_s < 0 or finish_s < 0 or exec_s < 0:
            raise ValueError("Waktu task tidak boleh negatif.")

        tasks_per_vm[vm_id] += 1

        exec_per_vm[vm_id] += exec_s

        finish_per_vm[vm_id] = max(
            finish_per_vm[vm_id],
            finish_s
        )

    # Makespan.
    makespan_s = float(makespan)

    # Rata-rata send_s sesuai kontrak tugas.
    avg_wait_s = (
        mean(float(record["send_s"]) for record in records)
        if records else 0.0
    )

    # Throughput.
    throughput = (
        n / makespan_s
        if makespan_s > 0 else 0.0
    )

    # Degree of Imbalance.
    avg_finish = (
        mean(finish_per_vm.values())
        if finish_per_vm else 0.0
    )

    if avg_finish > 0:
        di = (
            max(finish_per_vm.values())
            - min(finish_per_vm.values())
        ) / avg_finish
    else:
        di = 0.0

    # Utilisasi setiap worker.
    util_per_vm = {}

    for vm_id in vm_ids:
        denominator = cores[vm_id] * makespan_s

        if denominator > 0:
            util_per_vm[vm_id] = (
                exec_per_vm[vm_id]
                / denominator
                * 100
            )
        else:
            util_per_vm[vm_id] = 0.0

    # Rata-rata utilisasi seluruh worker.
    avg_util_pct = (
        mean(util_per_vm.values())
        if util_per_vm else 0.0
    )

    # Format hasil yang akan dipakai master.py.
    return {
        "n_tasks": n,
        "makespan_s": makespan_s,
        "avg_wait_s": avg_wait_s,
        "throughput": throughput,
        "DI": di,
        "avg_util_pct": avg_util_pct,
        "tasks_per_vm": tasks_per_vm,
        "util_per_vm": util_per_vm,
    }


# =========================================================
# 4. MENJELASKAN KEPUTUSAN MET
# =========================================================

def explain_met(
    lengths: list[float],
    capacity: dict[str, float],
    healthy: list[str],
    n: int = 3,
    tol: float = 0.05
) -> str:
    """
    Membuat tabel teks ET untuk n task pertama.
    """

    if n < 0:
        raise ValueError("Jumlah task untuk penjelasan tidak boleh negatif.")

    if tol < 0:
        raise ValueError("Toleransi tidak boleh negatif.")

    if not healthy:
        raise ValueError("Tidak ada worker sehat.")

    for vm_id in healthy:
        if vm_id not in capacity or capacity[vm_id] <= 0:
            raise ValueError(
                f"Kapasitas {vm_id} tidak tersedia atau tidak valid."
            )

    lines = [
        "PENJELASAN KEPUTUSAN MET",
        "-" * 72,
        f"{'Task':<10}{'Length':<12}"
        + "".join(f"{vm:<16}" for vm in healthy)
        + "Terpilih",
    ]

    for i, length in enumerate(lengths[:n], start=1):
        if length < 0:
            raise ValueError("Length task tidak boleh negatif.")

        et = {
            vm_id: length / capacity[vm_id]
            for vm_id in healthy
        }

        best = min(et.values())
        threshold = best * (1 + tol)

        selected = next(
            vm_id
            for vm_id in healthy
            if et[vm_id] <= threshold
        )

        row = (
            f"{i:<10}"
            f"{length:<12.2f}"
        )

        for vm_id in healthy:
            row += f"{et[vm_id]:<16.4f}"

        row += selected

        lines.append(row)

    return "\n".join(lines)


# =========================================================
# 5. PENGUJIAN OTOMATIS
# =========================================================

def selftest():
    print("Memulai pengujian scheduler.py...")

    # -----------------------------------------
    # Tes 1: Round Robin
    # -----------------------------------------

    workers = [
        "vm-worker-1",
        "vm-worker-2",
        "vm-worker-3",
        "vm-worker-4",
    ]

    result = rr(8, workers)

    expected = [
        "vm-worker-1",
        "vm-worker-2",
        "vm-worker-3",
        "vm-worker-4",
        "vm-worker-1",
        "vm-worker-2",
        "vm-worker-3",
        "vm-worker-4",
    ]

    assert result == expected, "Tes Round Robin gagal."

    print("PASS: Round Robin")

    # -----------------------------------------
    # Tes 2: MET memilih worker tercepat
    # -----------------------------------------

    capacity = {
        "vm-worker-1": 100.0,
        "vm-worker-2": 50.0,
        "vm-worker-3": 200.0,
        "vm-worker-4": 80.0,
    }

    lengths = [200.0, 100.0, 50.0]

    result = met(lengths, capacity, workers)

    assert result == [
        "vm-worker-3",
        "vm-worker-3",
        "vm-worker-3",
    ], "Tes MET gagal."

    print("PASS: MET memilih worker dengan ET minimum")

    # -----------------------------------------
    # Tes 3: Toleransi 5%
    # -----------------------------------------

    capacity_tie = {
        "vm-worker-1": 100.0,
        "vm-worker-2": 99.0,
    }

    result = met(
        [100.0],
        capacity_tie,
        ["vm-worker-1", "vm-worker-2"]
    )

    assert result == ["vm-worker-1"], (
        "Tes toleransi dan urutan worker gagal."
    )

    print("PASS: Toleransi 5% dan urutan worker")

    # -----------------------------------------
    # Tes 4: Worker tercepat tidak sehat
    # -----------------------------------------

    result = met(
        [100.0],
        capacity,
        [
            "vm-worker-1",
            "vm-worker-2",
            "vm-worker-4",
        ]
    )

    assert result == ["vm-worker-1"], (
        "Tes pemilihan worker sehat gagal."
    )

    print("PASS: MET menggunakan worker sehat")

    # -----------------------------------------
    # Tes 5: Metrik DI
    # -----------------------------------------

    records = [
        {
            "task_id": 1,
            "vm_id": "vm-worker-1",
            "send_s": 0.1,
            "finish_s": 10.0,
            "exec_s": 8.0,
        },
    ]

    metrics = compute_metrics(
        records,
        {
            "vm-worker-1": 1,
            "vm-worker-2": 1,
            "vm-worker-3": 1,
            "vm-worker-4": 1,
        },
        10.0,
    )

    assert abs(metrics["DI"] - 4.0) < 1e-9, (
        "Tes DI = 4 gagal."
    )

    print("PASS: Metrik DI empat worker")

    # -----------------------------------------
    # Tes 6: Selftest sesuai spesifikasi
    # -----------------------------------------

    rng = random.Random(42)

    capacities = {
        "vm-01": 1000.0,
        "vm-02": 1500.0,
        "vm-03": 2000.0,
        "vm-04": 2500.0,
    }

    # Lima worker untuk setiap tipe kapasitas.
    capacities_20 = {}

    for vm_type, capacity_value in capacities.items():
        for i in range(1, 6):
            vm_id = f"{vm_type}-{i}"
            capacities_20[vm_id] = capacity_value

    healthy_20 = list(capacities_20.keys())

    lengths_1000 = [
        rng.uniform(1.0, 10000.0)
        for _ in range(1000)
    ]

    result = met(
        lengths_1000,
        capacities_20,
        healthy_20,
    )

    fastest_capacity = max(capacities_20.values())

    fastest_workers = [
        vm_id
        for vm_id, cap in capacities_20.items()
        if cap == fastest_capacity
    ]

    assert all(
        vm_id in fastest_workers
        for vm_id in result
    ), "Tes distribusi MET pada 20 VM gagal."

    # DI model load pada selftest:
    # T_j = total length pada VM / kapasitas VM.
    total_length = sum(lengths_1000)

    loads = {
        vm_id: 0.0
        for vm_id in healthy_20
    }

    for length, vm_id in zip(lengths_1000, result):
        loads[vm_id] += length

    t_values = {
        vm_id: loads[vm_id] / capacities_20[vm_id]
        for vm_id in healthy_20
    }

    avg_t = mean(t_values.values())

    di_model = (
        (max(t_values.values()) - min(t_values.values()))
        / avg_t
        if avg_t > 0 else 0.0
    )

    assert abs(di_model - 20.0) < 1e-9, (
        f"DI model seharusnya 20.0, tetapi hasilnya {di_model}."
    )

    print("PASS: Selftest 20 VM dan 1000 task")
    print(f"DI model = {di_model:.1f}")

    print("\nSemua pengujian berhasil.")


# =========================================================
# 6. MENJALANKAN SELFTEST DARI TERMINAL
# =========================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scheduler MET dan Round Robin"
    )

    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Jalankan pengujian otomatis scheduler",
    )

    args = parser.parse_args()

    if args.selftest:
        selftest()
    else:
        parser.print_help()