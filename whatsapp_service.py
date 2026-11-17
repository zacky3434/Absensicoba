import os
from datetime import datetime
import httpx

# =====================================================================
# KONFIGURASI WHATSAPP OFFICIAL CLOUD API (META GRAPH API v20.0)
# =====================================================================
WA_CLOUD_API_TOKEN = os.getenv("WA_CLOUD_API_TOKEN", "")        # Isi Access Token Meta Developer
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "")        # Isi Phone Number ID WhatsApp Business
PESANTREN_NAME = "Pondok Pesantren Modern Al-Hikmah"
KONTAK_PENGASUHAN = "+62 811-2233-4455 (Kantor Pengasuhan Santri)"


def normalize_phone(phone: str) -> str:
    """Mengubah format nomor HP (08xx / +628xx) menjadi standar internasional Meta API (628xx)."""
    cleaned = "".join(filter(str.isdigit, phone))
    if cleaned.startswith("0"):
        cleaned = "62" + cleaned[1:]
    return cleaned


# =====================================================================
# KUMPULAN TEMPLATE PESAN KHUSUS PESANTREN
# =====================================================================
class PesantrenWATemplates:

    @staticmethod
    def attendance_checkin(
        parent_name: str,
        student_name: str,
        nis: str,
        dorm_room: str,
        shift_name: str,
        scan_time: str,
        status: str,
        auth_mode: str,
        points_added: int,
        total_points: int,
        was_offline: bool = False
    ) -> str:
        sync_badge = "🔄 *[Sinkronisasi Otomatis Mode Offline]*\n" if was_offline else ""
        status_icon = "✅ *TEPAT WAKTU*" if status == "TEPAT_WAKTU" else f"⚠️ *TERLAMBAT (+{points_added} Poin Ta'zir)*"

        return (
            f"🕌 *{PESANTREN_NAME.upper()}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{sync_badge}"
            f"Assalamu'alaikum Wr. Wb.\n"
            f"Yth. Bapak/Ibu *{parent_name}*,\n\n"
            f"Berikut informasi kehadiran harian putra/putri Anda di pondok:\n\n"
            f"👤 *Nama Santri* : {student_name} ({nis})\n"
            f"🏠 *Asrama/Kamar* : {dorm_room}\n"
            f"🕰️ *Kegiatan/Shift* : {shift_name}\n"
            f"⏱️ *Waktu Scan* : {scan_time} WIB\n"
            f"🔐 *Verifikasi* : {auth_mode}\n"
            f"📌 *Status Hadir* : {status_icon}\n"
            f"📊 *Akumulasi Poin* : {total_points} Poin\n\n"
            f"_Semoga Ananda senantiasa diberikan kesehatan, keistiqamahan, dan ilmu yang berkah. Aamiin._\n"
            f"Wassalamu'alaikum Wr. Wb."
        )

    @staticmethod
    def unexcused_alpha(
        parent_name: str,
        student_name: str,
        dorm_room: str,
        shift_name: str,
        points_added: int,
        total_points: int
    ) -> str:
        return (
            f"🕌 *{PESANTREN_NAME.upper()}*\n"
            f"⚠️ *PEMBERITAHUAN KETIDAKHADIRAN (ALFA)*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Assalamu'alaikum Wr. Wb.\n"
            f"Yth. Bapak/Ibu *{parent_name}*,\n\n"
            f"Kami informasikan bahwa hingga batas waktu penutupan presensi, Ananda:\n\n"
            f"👤 *Nama Santri* : {student_name}\n"
            f"🏠 *Asrama/Kamar* : {dorm_room}\n"
            f"🕰️ *Kegiatan* : {shift_name}\n"
            f"❌ *Status* : *ALFA (Tanpa Keterangan Izin/Sakit)*\n"
            f"⚖️ *Penambahan Poin* : +{points_added} Poin (Total: {total_points} Poin)\n\n"
            f"Ustadz/Musyrif asrama sedang melakukan pembinaan kepada Ananda. "
            f"Terima kasih atas kerja sama dan doa Bapak/Ibu.\n"
            f"Wassalamu'alaikum Wr. Wb."
        )

    @staticmethod
    def missing_student_emergency(
        parent_name: str,
        student_name: str,
        nis: str,
        dorm_room: str,
        consecutive_count: int,
        last_shift: str
    ) -> str:
        return (
            f"🚨 *PERINGATAN KEAMANAN & PENGASUHAN SANTRI* 🚨\n"
            f"*{PESANTREN_NAME.upper()}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Assalamu'alaikum Wr. Wb.\n"
            f"Yth. Bapak/Ibu *{parent_name}*,\n\n"
            f"Sistem Deteksi Dini Pesantren mencatat bahwa Ananda:\n\n"
            f"👤 *Nama Santri* : {student_name} ({nis})\n"
            f"🏠 *Asrama/Kamar* : {dorm_room}\n"
            f"⚠️ *Kondisi* : *Tidak melakukan scan biometrik selama {consecutive_count} shift kegiatan berturut-turut*\n"
            f"📍 *Shift Terakhir* : {last_shift}\n\n"
            f"Saat ini Tim Keamanan dan Musyrif Asrama sedang melakukan pengecekan langsung ke kamar asrama. "
            f"Apabila Bapak/Ibu memiliki informasi terkait Ananda, mohon segera menghubungi:\n"
            f"📞 *{KONTAK_PENGASUHAN}*\n\n"
            f"Wassalamu'alaikum Wr. Wb."
        )

    @staticmethod
    def leave_or_medical_permit(
        parent_name: str,
        student_name: str,
        dorm_room: str,
        leave_type: str,
        start_date: str,
        end_date: str,
        reason: str,
        approved_by: str
    ) -> str:
        header_label = "🏥 *INFORMASI POSKESTREN (SAKIT)*" if "SAKIT" in leave_type else "📝 *SURAT IZIN SANTRI TERPADU*"
        return (
            f"🕌 *{PESANTREN_NAME.upper()}*\n"
            f"{header_label}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Assalamu'alaikum Wr. Wb.\n"
            f"Yth. Bapak/Ibu *{parent_name}*,\n\n"
            f"Kami sampaikan pembaruan status perizinan/kesehatan Ananda:\n\n"
            f"👤 *Nama Santri* : {student_name}\n"
            f"🏠 *Asrama/Kamar* : {dorm_room}\n"
            f"🏷️ *Jenis Izin* : *{leave_type}*\n"
            f"📅 *Masa Berlaku* : {start_date} s/d {end_date}\n"
            f"🩺 *Keterangan* : {reason}\n"
            f"✍️ *Disetujui Oleh* : {approved_by}\n\n"
            f"_Selama masa izin/istirahat ini, sistem otomatis membebaskan Ananda dari poin alfa pada jadwal kegiatan._\n"
            f"Wassalamu'alaikum Wr. Wb."
        )

    @staticmethod
    def bk_early_warning_counseling(
        parent_name: str,
        student_name: str,
        dorm_room: str,
        risk_score: float,
        level: str,
        factors: list,
        recommendation: str
    ) -> str:
        factors_bullet = "\n".join([f"  • {f}" for f in factors])
        return (
            f"🕌 *{PESANTREN_NAME.upper()}*\n"
            f"🧠 *LAPORAN BIMBINGAN KONSELING (EARLY WARNING)*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Assalamu'alaikum Wr. Wb.\n"
            f"Yth. Bapak/Ibu *{parent_name}*,\n\n"
            f"Berdasarkan evaluasi sistem pemantauan adab & kedisiplinan 14 hari terakhir, "
            f"kami menyampaikan laporan pembinaan untuk Ananda:\n\n"
            f"👤 *Nama Santri* : {student_name}\n"
            f"🏠 *Asrama/Kamar* : {dorm_room}\n"
            f"📈 *Skor Indeks BK* : {risk_score} / 100 ({level})\n\n"
            f"🔍 *Catatan Perubahan Pola:*\n{factors_bullet}\n\n"
            f"💡 *Tindak Lanjut Pengasuhan:*\n{recommendation}\n\n"
            f"Mari bersama-sama mendampingi dan mendoakan Ananda agar semakin semangat dalam menuntut ilmu.\n"
            f"Wassalamu'alaikum Wr. Wb."
        )


# =====================================================================
# PENGIRIM PESAN KE META WHATSAPP CLOUD API
# =====================================================================
async def send_official_whatsapp(
    recipient_phone: str,
    formatted_message: str,
    template_name: str = None,
    template_params: list = None
) -> str:
    """
    Mengirimkan pesan ke Meta WhatsApp Official API.
    Jika WA_CLOUD_API_TOKEN belum diisi, sistem berjalan dalam Mode Simulasi Lokal
    dan tetap mencatat pesan lengkap ke dashboard aplikasi.
    """
    normalized_to = normalize_phone(recipient_phone)

    if not WA_CLOUD_API_TOKEN or not WA_PHONE_NUMBER_ID:
        return "TERKIRIM (Simulasi Official WA API)"

    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_CLOUD_API_TOKEN}",
        "Content-Type": "application/json"
    }

    # Jika menggunakan Meta Approved Utility Template
    if template_name and template_params:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "id"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": str(p)} for p in template_params]
                    }
                ]
            }
        }
    else:
        # Pengiriman format teks lengkap
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalized_to,
            "type": "text",
            "text": {"preview_url": False, "body": formatted_message}
        }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code in (200, 201):
                return f"TERKIRIM KE META API ({datetime.now().strftime('%H:%M')})"
            return f"GAGAL_API ({response.status_code})"
    except Exception:
        return "ANTREAN_RETRY_OFFLINE"