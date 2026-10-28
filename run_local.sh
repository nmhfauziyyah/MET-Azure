
#!/usr/bin/env bash
set -euo pipefail

# Pindah ke folder utama proyek.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Gunakan Python dari virtual environment yang sudah dibuat.
if [[ -x "$ROOT_DIR/.venv/Scripts/python.exe" ]]; then
    PYTHON="$ROOT_DIR/.venv/Scripts/python.exe"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON="$ROOT_DIR/.venv/bin/python"
else
    echo "ERROR: Python .venv tidak ditemukan."
    echo "Pastikan virtual environment sudah dibuat."
    exit 1
fi

LOG_DIR="$ROOT_DIR/logs/local-workers"
mkdir -p "$LOG_DIR"

PIDS=()
CLEANED=0

# Menghentikan semua worker saat Ctrl+C.
cleanup() {
    if [[ "$CLEANED" -eq 1 ]]; then
        return
    fi

    CLEANED=1
    echo ""
    echo "Menghentikan semua worker..."

    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done

    for pid in "${PIDS[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    echo "Semua proses worker sudah diminta berhenti."
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Menjalankan satu worker di background.
start_worker() {
    local vm_id="$1"
    local cores="$2"
    local port="$3"

    echo "Menjalankan $vm_id pada port $port..."

    WORKER_CORES="$cores" VM_ID="$vm_id" \
        "$PYTHON" -m uvicorn worker:app \
        --host 127.0.0.1 \
        --port "$port" \
        > "$LOG_DIR/$vm_id.log" 2>&1 &

    PIDS+=("$!")
}

# Host A.
start_worker vm-worker-1 2 8001
start_worker vm-worker-2 1 8002

# Host B.
start_worker vm-worker-3 2 8003
start_worker vm-worker-4 1 8004

sleep 2

echo ""
echo "Permintaan menjalankan empat worker sudah dikirim."
echo "Log worker tersimpan di: $LOG_DIR"
echo ""
echo "Periksa endpoint /health dari terminal lain."
echo "Tekan Ctrl+C di terminal ini untuk menghentikan worker."

# Menjaga script tetap berjalan agar Ctrl+C menghentikan worker.
wait
