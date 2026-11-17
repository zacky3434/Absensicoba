import argparse
import sqlite3
import time
from datetime import datetime
import httpx

try:
    from zk import ZK, const
    PYZK_AVAILABLE = True
except ImportError:
    PYZK_AVAILABLE = False

# =====================================================================
# KONFIGURASI MESIN FINGERPRINT ZKTECO & SERVER
# =====================================================================
ZK_DEVICE_IP = "192.168.1.201"          # IP Address Mesin Fingerprint ZKTeco di LAN Pesantren
ZK_DEVICE_PORT = 4370                   # Port standar protokol TCP/IP ZKTeco
ZK_COMM_PASSWORD = 0                    # Comm Key / Password Mesin (Default: 0)
BACKEND_BASE_URL = "http://127.0.0.1:8000"
LOCAL_BUFFER_DB = "edge_offline_buffer.db"

# Pemetaan User ID di mesin ZKTeco ke Fingerprint ID di Database Server
ZK_USER_MAP = {
    "1": "FP-001",
    "2": "FP-002",
    "3": "FP-003",
    "4": "FP-004",
}

# Pemetaan kode tipe verifikasi bawaan hardware ZKTeco
ZK_PUNCH_METHOD_MAP = {
    0: "PIN_BACKUP",          # Password / PIN di keypad mesin
    1: "FINGERPRINT",         # Sidik jari murni (1:N)
    3: "PIN_BACKUP",          # PIN
    4: "DUAL_VERIFICATION",   # Kombinasi Sidik Jari + PIN di mesin
    15: "FINGERPRINT"         # Wajah / Biometrik gabungan
}


# =====================================================================
# 1. INISIALISASI SQLITE BUFFER LOKAL (UNTUK MODE OFFLINE)
# =====================================================================
def init_local_buffer():
    conn = sqlite3.connect(LOCAL_BUFFER_DB)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS offline_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identifier TEXT NOT NULL,
            shift_id INTEGER NOT NULL,
            auth_mode TEXT NOT NULL,
            scan_time_str TEXT NOT NULL,
            hardware_ts TEXT NOT NULL,
            notes TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_to_local_buffer(payload: dict, hardware_ts: str):
    conn = sqlite3.connect(LOCAL_BUFFER_DB)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO offline_queue (identifier, shift_id, auth_mode, scan_time_str, hardware_ts, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        payload["identifier"],
        payload["shift_id"],
        payload["auth_mode"],
        payload["scan_time_str"],
        hardware_ts,
        payload.get("notes", "Buffered from ZKTeco Edge")
    ))
    conn.commit()
    conn.close()
    print(f"📦 [OFFLINE BUFFER] Data disimpan ke SQLite lokal: {payload['identifier']} ({payload['scan_time_str']})")


def flush_offline_buffer_to_server():
    """Mengunggah antrean lokal ke server segera setelah koneksi pulih."""
    conn = sqlite3.connect(LOCAL_BUFFER_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT id, identifier, shift_id, auth_mode, scan_time_str, notes FROM offline_queue ORDER BY id ASC")
    rows = cursor.fetchall()

    if not rows:
        conn.close()
        return

    records = [
        {
            "identifier": r[1],
            "shift_id": r[2],
            "auth_mode": r[3],
            "scan_time_str": r[4],
            "was_offline": True,
            "notes": r[5]
        }
        for r in rows
    ]

    try:
        with httpx.Client(timeout=5.0) as client:
            res = client.post(f"{BACKEND_BASE_URL}/api/attendance/sync-offline", json={"records": records})
            if res.status_code == 200:
                cursor.execute("DELETE FROM offline_queue")
                conn.commit()
                print(f"✅ [AUTO-SYNC] {len(records)} log offline ZKTeco berhasil disinkronkan ke Server!")
    except Exception:
        pass
    finally:
        conn.close()


# =====================================================================
# 2. PENENTU SHIFT OTOMATIS BERDASARKAN JAM SCAN MESIN
# =====================================================================
def resolve_active_shift_id(scan_dt: datetime) -> int:
    hhmm = scan_dt.strftime("%H:%M")
    if "03:30" <= hhmm <= "05:45":
        return 1  # Shalat Subuh Berjamaah
    elif "06:00" <= hhmm <= "14:00":
        return 2  # KBM Sekolah Pagi
    elif "17:30" <= hhmm <= "20:15":
        return 3  # Halaqah & Tahfidz Maghrib
    else:
        return 4  # Apel Malam (Curfew Asrama)


# =====================================================================
# 3. PENGIRIM EVENT KE SERVER FASTAPI
# =====================================================================
def push_attendance_event(user_id_str: str, scan_dt: datetime, status_code: int):
    # Terjemahkan User ID mesin ke Fingerprint ID santri (misal "1" -> "FP-001")
    identifier = ZK_USER_MAP.get(str(user_id_str), str(user_id_str))
    shift_id = resolve_active_shift_id(scan_dt)
    auth_mode = ZK_PUNCH_METHOD_MAP.get(status_code, "FINGERPRINT")
    scan_time_str = scan_dt.strftime("%H:%M")

    payload = {
        "identifier": identifier,
        "shift_id": shift_id,
        "auth_mode": auth_mode,
        "scan_time_str": scan_time_str,
        "was_offline": False,
        "notes": f"ZKTeco Terminal ({ZK_DEVICE_IP}) @ {scan_dt.strftime('%H:%M:%S')}"
    }

    try:
        with httpx.Client(timeout=4.0) as client:
            # Sinkronkan antrean lama terlebih dahulu jika ada
            flush_offline_buffer_to_server()

            response = client.post(f"{BACKEND_BASE_URL}/api/attendance/scan", json=payload)
            if response.status_code == 200:
                data = response.json()
                print(f"🟢 [REAL-TIME ZK] {data['message']}")
            elif response.status_code == 409:
                print(f"🛡️ [ANTI-PASSBACK] Ditolak Server: {response.json().get('detail')}")
            else:
                print(f"⚠️ [RESPON SERVER {response.status_code}] {response.text}")
    except httpx.RequestError:
        # Jika server/internet terputus, simpan otomatis ke SQLite lokal
        save_to_local_buffer(payload, scan_dt.isoformat())


# =====================================================================
# 4. DAEMON UTAMA MESIN FISIK ZKTECO & MODE SIMULASI
# =====================================================================
def run_zkteco_live_daemon():
    if not PYZK_AVAILABLE:
        print("❌ Pustaka 'pyzk' belum terinstal. Jalankan: pip install pyzk")
        return

    zk = ZK(ZK_DEVICE_IP, port=ZK_DEVICE_PORT, timeout=10, password=ZK_COMM_PASSWORD, force_udp=False, ommit_ping=False)
    print(f"🔌 Menghubungkan ke Mesin Fingerprint ZKTeco di {ZK_DEVICE_IP}:{ZK_DEVICE_PORT}...")

    while True:
        conn = None
        try:
            conn = zk.connect()
            print(f"✅ Terhubung ke ZKTeco! Firmware: {conn.get_firmware_version()} | Serial: {conn.get_serialnumber()}")
            print("👂 Mendengarkan scan sidik jari santri secara Real-Time (Live Capture)...")

            for attendance in conn.live_capture():
                if attendance is None:
                    # Heartbeat rutin: cek apakah ada data offline yang perlu di-sync
                    flush_offline_buffer_to_server()
                else:
                    print(f"👆 Scan Terdeteksi -> UserID: {attendance.user_id} | Waktu: {attendance.timestamp} | Metode: {attendance.status}")
                    push_attendance_event(
                        user_id_str=str(attendance.user_id),
                        scan_dt=attendance.timestamp,
                        status_code=attendance.status
                    )
        except Exception as e:
            print(f"⚠️ Koneksi ke mesin ZKTeco terputus ({e}). Mencoba menghubungkan ulang dalam 5 detik...")
            time.sleep(5)
        finally:
            if conn:
                try:
                    conn.disconnect()
                except Exception:
                    pass


def run_simulation_mode():
    """Mode pengujian di PyCharm tanpa perlu mesin fisik ZKTeco."""
    print("🧪 [MODE SIMULASI ZKTECO AKTIF] Mengirim simulasi tap sidik jari santri ke server...")
    sample_events = [
        ("FP-001", datetime.now().replace(hour=4, minute=28), 1),  # Ahmad Fauzan - Tepat Waktu Subuh
        ("FP-002", datetime.now().replace(hour=4, minute=45), 1),  # M. Rizky - Terlambat Subuh
        ("FP-001", datetime.now().replace(hour=4, minute=30), 1),  # Ahmad Fauzan - Tes Anti-Passback (Ditolak)
    ]
    for fp_id, fake_dt, method_code in sample_events:
        push_attendance_event(fp_id, fake_dt, method_code)
        time.sleep(1.5)


if __name__ == "__main__":
    init_local_buffer()
    parser = argparse.ArgumentParser(description="Konektor Mesin Fingerprint ZKTeco SIM-Pontren")
    parser.add_argument("--simulate", action="store_true", help="Jalankan simulasi tanpa mesin fisik ZKTeco")
    args = parser.parse_args()

    if args.simulate:
        run_simulation_mode()
    else:
        run_zkteco_live_daemon()