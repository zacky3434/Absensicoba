import base64
import io
import random
import re
from datetime import datetime, date
from typing import List, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# Import modul WhatsApp Pesantren (dengan fallback otomatis)
try:
    from whatsapp_service import PesantrenWATemplates, send_official_whatsapp
    WA_MODULE_AVAILABLE = True
except ImportError:
    WA_MODULE_AVAILABLE = False

# ==========================================
# 1. KONFIGURASI DATABASE (SQLITE)
# ==========================================
DATABASE_URL = "sqlite:///./sim_pontren.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

DEFAULT_DORM_PHOTO_URL = "https://images.unsplash.com/photo-1541339907198-e08756dedf3f?auto=format&fit=crop&w=1600&q=80"


# ==========================================
# 2. MODEL DATABASE (TABEL INTI + PENGATURAN)
# ==========================================
class SystemSetting(Base):
    __tablename__ = "system_settings"
    key = Column(String(50), primary_key=True, index=True)
    value = Column(Text, nullable=False)


class UserAccount(Base):
    __tablename__ = "user_accounts"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password = Column(String(100), nullable=False)
    full_name = Column(String(100), nullable=False)
    role = Column(String(20), nullable=False)          # ADMIN, USTADZ, SANTRI
    role_title = Column(String(80), nullable=False)
    linked_student_id = Column(Integer, nullable=True)


class Student(Base):
    __tablename__ = "students"
    id = Column(Integer, primary_key=True, index=True)
    nis = Column(String(20), unique=True, index=True)
    name = Column(String(100), nullable=False)
    dorm_room = Column(String(50), nullable=False)
    classroom = Column(String(30), nullable=False)
    fingerprint_id = Column(String(50), unique=True)
    backup_pin = Column(String(10), nullable=False)
    parent_name = Column(String(100), nullable=False)
    parent_wa = Column(String(25), nullable=False)
    discipline_points = Column(Integer, default=0)
    consecutive_absent = Column(Integer, default=0)
    late_count_14d = Column(Integer, default=0)
    sakit_count_14d = Column(Integer, default=0)
    pin_usage_14d = Column(Integer, default=0)


class BKEarlyWarningRecord(Base):
    __tablename__ = "bk_early_warning_records"
    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    nis = Column(String(20))
    student_name = Column(String(100))
    dorm_room = Column(String(50))
    backup_pin = Column(String(10))
    late_subuh_curfew = Column(Integer, default=0)
    sakit_freq = Column(Integer, default=0)
    alfa_count = Column(Integer, default=0)
    risk_score = Column(Float, default=0.0)
    level = Column(String(50))
    indicators = Column(Text)
    recommendation = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


class ShiftSchedule(Base):
    __tablename__ = "shift_schedules"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)
    start_time = Column(String(10), nullable=False)
    on_time_limit = Column(String(10), nullable=False)
    end_time = Column(String(10), nullable=False)
    late_points = Column(Integer, default=2)
    absent_points = Column(Integer, default=5)


class AttendanceLog(Base):
    __tablename__ = "attendance_logs"
    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    student_name = Column(String(100))
    shift_id = Column(Integer, ForeignKey("shift_schedules.id"))
    shift_name = Column(String(100))
    timestamp = Column(DateTime, default=datetime.now)
    method = Column(String(40))
    status = Column(String(30))
    was_offline = Column(Boolean, default=False)
    wa_sent = Column(Boolean, default=True)
    notes = Column(String(255), default="-")


class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    student_name = Column(String(100))
    leave_type = Column(String(30))
    reason = Column(Text)
    start_date = Column(String(20))
    end_date = Column(String(20))
    approved_by = Column(String(100))
    status = Column(String(20), default="AKTIF")


class InventoryLoan(Base):
    __tablename__ = "inventory_loans"
    id = Column(Integer, primary_key=True, index=True)
    item_code = Column(String(30))
    item_name = Column(String(100))
    location = Column(String(50))
    student_id = Column(Integer, ForeignKey("students.id"))
    student_name = Column(String(100))
    borrowed_at = Column(DateTime, default=datetime.now)
    returned_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="DIPINJAM")


class WhatsAppOutbox(Base):
    __tablename__ = "whatsapp_outbox"
    id = Column(Integer, primary_key=True, index=True)
    parent_wa = Column(String(25))
    student_name = Column(String(100))
    message = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    delivery_status = Column(String(40), default="TERKIRIM (Official WA API)")


Base.metadata.create_all(bind=engine)


# ==========================================
# 3. SKEMA REQUEST PYDANTIC
# ==========================================
class LoginRequest(BaseModel):
    role: str
    username: str
    password: str


class AccountCreateRequest(BaseModel):
    username: str
    password: str
    full_name: str
    role: str
    role_title: str


class AccountUpdateRequest(BaseModel):
    account_id: int
    username: str
    password: str
    full_name: str


class DormPhotoUpdateRequest(BaseModel):
    photo_data: str


class ShiftCreateRequest(BaseModel):
    name: str
    category: str
    start_time: str
    on_time_limit: str
    end_time: str
    late_points: int = 2
    absent_points: int = 5


class StudentCreateRequest(BaseModel):
    name: str
    dorm_room: str
    classroom: str
    parent_name: str
    parent_wa: str
    custom_username: Optional[str] = None
    custom_password: Optional[str] = "santri123"
    custom_pin: Optional[str] = None


class StudentProfileUpdateRequest(BaseModel):
    name: str
    dorm_room: str
    classroom: str
    parent_name: str
    parent_wa: str
    discipline_points: int = 0


class ExcelImportRequest(BaseModel):
    file_base64: str


class StudentPinUpdateRequest(BaseModel):
    student_id: int
    new_pin: str
    actor_role: str
    actor_name: str


class BKManualCreateRequest(BaseModel):
    student_id: int
    late_subuh_curfew: int = 0
    sakit_freq: int = 0
    alfa_count: int = 0
    custom_risk_score: Optional[float] = None
    level: Optional[str] = None
    indicators: Optional[str] = None
    recommendation: Optional[str] = None


class ScanRequest(BaseModel):
    identifier: str
    shift_id: int
    auth_mode: str = "FINGERPRINT"
    pin_code: Optional[str] = None
    scan_time_str: Optional[str] = None
    was_offline: bool = False
    notes: Optional[str] = "-"


class OfflineBatchRequest(BaseModel):
    records: List[ScanRequest]


class LeaveCreateRequest(BaseModel):
    student_id: int
    leave_type: str
    reason: str
    start_date: str
    end_date: str
    approved_by: str


class InventoryCreateRequest(BaseModel):
    item_code: str
    item_name: str
    location: str
    student_id: int


class UnexcusedSweepRequest(BaseModel):
    student_id: int
    shift_id: int


# ==========================================
# 4. INISIALISASI APLIKASI
# ==========================================
app = FastAPI(title="SIM-Pontren Biometric & AI Management System")
templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def generate_unique_pin(db: Session) -> str:
    while True:
        candidate = str(random.randint(100000, 999999))
        exists = db.query(Student).filter(Student.backup_pin == candidate).first()
        if not exists:
            return candidate


def get_next_student_codes(db: Session):
    existing_students = db.query(Student).all()
    max_num = 0
    for s in existing_students:
        m = re.search(r"(\d+)$", s.nis or "")
        if m:
            max_num = max(max_num, int(m.group(1)))
    next_num = max_num + 1
    return f"SNT-2026-{next_num:03d}", f"FP-{next_num:03d}", next_num


def init_system_defaults():
    db = SessionLocal()
    if db.query(UserAccount).filter(UserAccount.role == "ADMIN").count() == 0:
        admin_acc = UserAccount(
            username="admin",
            password="admin123",
            full_name="Zacky (Creator & Super Admin)",
            role="ADMIN",
            role_title="Pembuat Aplikasi & Administrator Utama",
            linked_student_id=None
        )
        db.add(admin_acc)

    if not db.query(SystemSetting).filter(SystemSetting.key == "dorm_photo_url").first():
        db.add(SystemSetting(key="dorm_photo_url", value=DEFAULT_DORM_PHOTO_URL))

    db.commit()
    db.close()


init_system_defaults()


# ==========================================
# 5. HELPER DISPATCH WHATSAPP PESANTREN
# ==========================================
async def dispatch_whatsapp_notification(db: Session, student: Student, message: str):
    delivery_status = "TERKIRIM (Official WA API)"
    if WA_MODULE_AVAILABLE:
        delivery_status = await send_official_whatsapp(
            recipient_phone=student.parent_wa,
            formatted_message=message
        )
    wa_log = WhatsAppOutbox(
        parent_wa=student.parent_wa,
        student_name=student.name,
        message=message,
        delivery_status=delivery_status
    )
    db.add(wa_log)
    db.commit()


# ==========================================
# 6. ANALITIK BULANAN MURNI DARI DATABASE
# ==========================================
def build_monthly_analytics(db: Session) -> dict:
    shifts = db.query(ShiftSchedule).all()
    logs = db.query(AttendanceLog).all()
    leaves_count = db.query(LeaveRequest).count()

    if not shifts:
        return {
            "shift_labels": ["Belum Ada Shift"],
            "on_time_series": [0],
            "late_series": [0],
            "alpha_series": [0],
            "weekly_labels": ["Minggu Ke-1", "Minggu Ke-2", "Minggu Ke-3", "Minggu Ke-4"],
            "weekly_ontime_pct": [0, 0, 0, 0],
            "weekly_late_pct": [0, 0, 0, 0],
            "summary_totals": {
                "tepat_waktu": 0,
                "terlambat": 0,
                "alfa": 0,
                "izin_sakit": leaves_count
            }
        }

    shift_labels = []
    on_time_series = []
    late_series = []
    alpha_series = []

    for sh in shifts:
        shift_labels.append(sh.name)
        sh_logs = [lg for lg in logs if lg.shift_id == sh.id]
        on_time_series.append(sum(1 for lg in sh_logs if lg.status == "TEPAT_WAKTU"))
        late_series.append(sum(1 for lg in sh_logs if lg.status == "TERLAMBAT"))
        alpha_series.append(sum(1 for lg in sh_logs if lg.status == "ALFA"))

    total_ontime = sum(on_time_series)
    total_late = sum(late_series)
    total_alpha = sum(alpha_series)
    grand_total = total_ontime + total_late + total_alpha

    ontime_pct = round((total_ontime / grand_total) * 100, 1) if grand_total > 0 else 0
    late_pct = round((total_late / grand_total) * 100, 1) if grand_total > 0 else 0

    return {
        "shift_labels": shift_labels,
        "on_time_series": on_time_series,
        "late_series": late_series,
        "alpha_series": alpha_series,
        "weekly_labels": ["Minggu Ke-1", "Minggu Ke-2", "Minggu Ke-3", "Minggu Ke-4"],
        "weekly_ontime_pct": [0, 0, 0, ontime_pct],
        "weekly_late_pct": [0, 0, 0, late_pct],
        "summary_totals": {
            "tepat_waktu": total_ontime,
            "terlambat": total_late,
            "alfa": total_alpha,
            "izin_sakit": leaves_count
        }
    }


# ==========================================
# 7. ENDPOINT LOGIN, AKUN, SHIFT & FOTO ASRAMA
# ==========================================
@app.post("/api/auth/login")
def login_user(req: LoginRequest, db: Session = Depends(get_db)):
    account = db.query(UserAccount).filter(
        UserAccount.username == req.username.strip(),
        UserAccount.role == req.role.upper()
    ).first()

    if not account or account.password != req.password:
        raise HTTPException(
            status_code=401,
            detail=f"Username atau Password tidak sesuai untuk jalur login {req.role}."
        )

    return {
        "id": account.id,
        "username": account.username,
        "full_name": account.full_name,
        "role": account.role,
        "role_title": account.role_title,
        "linked_student_id": account.linked_student_id
    }


@app.post("/api/settings/dorm-photo")
def update_dorm_photo(req: DormPhotoUpdateRequest, db: Session = Depends(get_db)):
    clean_photo = req.photo_data.strip() if req.photo_data else DEFAULT_DORM_PHOTO_URL
    setting = db.query(SystemSetting).filter(SystemSetting.key == "dorm_photo_url").first()
    if not setting:
        setting = SystemSetting(key="dorm_photo_url", value=clean_photo)
        db.add(setting)
    else:
        setting.value = clean_photo
    db.commit()
    return {"message": "Foto Asrama PeTIK Jombang pada tampilan Login berhasil diperbarui!", "dorm_photo_url": clean_photo}


@app.post("/api/accounts/create")
def create_staff_account(req: AccountCreateRequest, db: Session = Depends(get_db)):
    role_upper = req.role.upper().strip()
    if role_upper not in ("USTADZ", "ADMIN"):
        raise HTTPException(status_code=400, detail="Peran akun staff harus USTADZ atau ADMIN.")

    clean_user = req.username.strip()
    if db.query(UserAccount).filter(UserAccount.username == clean_user).first():
        raise HTTPException(status_code=400, detail="Username sudah digunakan. Pilih username lain.")

    new_acc = UserAccount(
        username=clean_user,
        password=req.password.strip(),
        full_name=req.full_name.strip(),
        role=role_upper,
        role_title=req.role_title.strip() or ("Musyrif & Ustadz" if role_upper == "USTADZ" else "Administrator"),
        linked_student_id=None
    )
    db.add(new_acc)
    db.commit()
    return {"message": f"Akun {role_upper} untuk {new_acc.full_name} ({clean_user}) berhasil dibuat!"}


@app.post("/api/accounts/update")
def update_account_credentials(req: AccountUpdateRequest, db: Session = Depends(get_db)):
    acc = db.query(UserAccount).filter(UserAccount.id == req.account_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Akun tidak ditemukan.")

    duplicate = db.query(UserAccount).filter(
        UserAccount.username == req.username.strip(),
        UserAccount.id != req.account_id
    ).first()
    if duplicate:
        raise HTTPException(status_code=400, detail="Username tersebut sudah digunakan akun lain.")

    acc.username = req.username.strip()
    acc.password = req.password.strip()
    acc.full_name = req.full_name.strip()
    db.commit()
    return {"message": f"Kredensial akun {acc.full_name} ({acc.role}) berhasil diperbarui!"}


@app.post("/api/shifts/create")
def create_shift_schedule(req: ShiftCreateRequest, db: Session = Depends(get_db)):
    new_shift = ShiftSchedule(
        name=req.name.strip(),
        category=req.category.strip().upper(),
        start_time=req.start_time.strip(),
        on_time_limit=req.on_time_limit.strip(),
        end_time=req.end_time.strip(),
        late_points=max(0, int(req.late_points)),
        absent_points=max(0, int(req.absent_points))
    )
    db.add(new_shift)
    db.commit()
    return {"message": f"Jadwal shift '{new_shift.name}' berhasil ditambahkan."}


@app.put("/api/shifts/{shift_id}")
def update_shift_schedule(shift_id: int, req: ShiftCreateRequest, db: Session = Depends(get_db)):
    """Memperbarui jadwal shift (nama, kategori, jam buka, batas tepat waktu, jam tutup, dan poin pelanggaran)."""
    shift = db.query(ShiftSchedule).filter(ShiftSchedule.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Jadwal shift tidak ditemukan.")

    shift.name = req.name.strip()
    shift.category = req.category.strip().upper()
    shift.start_time = req.start_time.strip()
    shift.on_time_limit = req.on_time_limit.strip()
    shift.end_time = req.end_time.strip()
    shift.late_points = max(0, int(req.late_points))
    shift.absent_points = max(0, int(req.absent_points))

    # Sinkronkan perubahan nama shift pada riwayat log kehadiran terkait
    for lg in db.query(AttendanceLog).filter(AttendanceLog.shift_id == shift.id).all():
        lg.shift_name = shift.name

    db.commit()
    return {"message": f"Jadwal shift '{shift.name}' berhasil diperbarui!"}


@app.delete("/api/shifts/{shift_id}")
def delete_shift_schedule(shift_id: int, db: Session = Depends(get_db)):
    """Menghapus jadwal shift kegiatan."""
    shift = db.query(ShiftSchedule).filter(ShiftSchedule.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Jadwal shift tidak ditemukan.")

    shift_name = shift.name
    db.delete(shift)
    db.commit()
    return {"message": f"Jadwal shift '{shift_name}' berhasil dihapus."}


# ==========================================
# 8. ENDPOINT CRUD SANTRI (TAMBAH, EDIT, IMPORT, HAPUS)
# ==========================================
def register_single_student_helper(
    db: Session,
    name: str,
    dorm_room: str,
    classroom: str,
    parent_name: str,
    parent_wa: str,
    custom_username: str = None,
    custom_password: str = "santri123",
    custom_pin: str = None
):
    nis, fingerprint_id, next_num = get_next_student_codes(db)

    pin_code = (custom_pin or "").strip()
    if pin_code and pin_code.isdigit() and len(pin_code) == 6:
        if db.query(Student).filter(Student.backup_pin == pin_code).first():
            pin_code = generate_unique_pin(db)
    else:
        pin_code = generate_unique_pin(db)

    base_username = (custom_username or "").strip().lower()
    if not base_username:
        first_word = re.sub(r"[^a-zA-Z0-9]", "", name.split()[0]).lower() if name.split() else ""
        base_username = first_word or f"santri{next_num}"

    final_username = base_username
    suffix = 1
    while db.query(UserAccount).filter(UserAccount.username == final_username).first():
        suffix += 1
        final_username = f"{base_username}{suffix}"

    password_val = (custom_password or "santri123").strip()

    new_student = Student(
        nis=nis,
        name=name.strip(),
        dorm_room=dorm_room.strip(),
        classroom=classroom.strip(),
        fingerprint_id=fingerprint_id,
        backup_pin=pin_code,
        parent_name=parent_name.strip(),
        parent_wa=parent_wa.strip(),
        discipline_points=0,
        consecutive_absent=0
    )
    db.add(new_student)
    db.commit()
    db.refresh(new_student)

    new_account = UserAccount(
        username=final_username,
        password=password_val,
        full_name=new_student.name,
        role="SANTRI",
        role_title=f"Santri - {new_student.dorm_room}",
        linked_student_id=new_student.id
    )
    db.add(new_account)
    db.commit()
    return new_student, final_username, password_val, pin_code


@app.post("/api/students/create")
async def create_student_and_auto_account(req: StudentCreateRequest, db: Session = Depends(get_db)):
    pin_check = (req.custom_pin or "").strip()
    if pin_check:
        if not (pin_check.isdigit() and len(pin_check) == 6):
            raise HTTPException(status_code=400, detail="PIN Santri harus berupa 6 digit angka.")
        if db.query(Student).filter(Student.backup_pin == pin_check).first():
            raise HTTPException(status_code=400, detail="PIN 6-digit tersebut sudah dipakai santri lain.")

    new_student, final_username, password_val, pin_code = register_single_student_helper(
        db=db,
        name=req.name,
        dorm_room=req.dorm_room,
        classroom=req.classroom,
        parent_name=req.parent_name,
        parent_wa=req.parent_wa,
        custom_username=req.custom_username,
        custom_password=req.custom_password,
        custom_pin=req.custom_pin
    )

    wa_msg = (
        f"🕌 *REGISTRASI BIOMETRIK & AKUN SANTRI BERHASIL*\n"
        f"Assalamu'alaikum Wr. Wb. Yth. *{new_student.parent_name}*,\n\n"
        f"Ananda *{new_student.name}* telah terdaftar di sistem SIM-Pontren:\n"
        f"• *NIS* : {new_student.nis}\n"
        f"• *Asrama / Kelas* : {new_student.dorm_room} / {new_student.classroom}\n"
        f"• *ID Sidik Jari* : {new_student.fingerprint_id}\n"
        f"• *PIN Backup (6-Digit)* : {new_student.backup_pin}\n"
        f"• *Username Login Portal* : `{final_username}`\n"
        f"• *Password Login* : `{password_val}`"
    )
    await dispatch_whatsapp_notification(db, new_student, wa_msg)

    return {
        "message": (
            f"Santri {new_student.name} ({new_student.nis}) berhasil ditambahkan! "
            f"Akun: '{final_username}' | Password: '{password_val}' | PIN: '{pin_code}'."
        )
    }


@app.put("/api/students/{student_id}")
def update_student_profile(student_id: int, req: StudentProfileUpdateRequest, db: Session = Depends(get_db)):
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Data santri tidak ditemukan.")

    student.name = req.name.strip()
    student.dorm_room = req.dorm_room.strip()
    student.classroom = req.classroom.strip()
    student.parent_name = req.parent_name.strip()
    student.parent_wa = req.parent_wa.strip()
    student.discipline_points = max(0, int(req.discipline_points))

    linked_acc = db.query(UserAccount).filter(UserAccount.linked_student_id == student.id).first()
    if linked_acc:
        linked_acc.full_name = student.name
        linked_acc.role_title = f"Santri - {student.dorm_room}"

    for lg in db.query(AttendanceLog).filter(AttendanceLog.student_id == student.id).all():
        lg.student_name = student.name
    for lv in db.query(LeaveRequest).filter(LeaveRequest.student_id == student.id).all():
        lv.student_name = student.name
    for ln in db.query(InventoryLoan).filter(InventoryLoan.student_id == student.id).all():
        ln.student_name = student.name
    for bk in db.query(BKEarlyWarningRecord).filter(BKEarlyWarningRecord.student_id == student.id).all():
        bk.student_name = student.name
        bk.dorm_room = student.dorm_room

    db.commit()
    return {"message": f"Profil santri {student.name} ({student.nis}) berhasil diperbarui!"}


@app.get("/api/students/template-excel")
def download_student_excel_template():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Template Import Santri"

    headers = [
        "Nama Lengkap Santri",
        "Kamar Asrama",
        "Kelas Sekolah",
        "Nama Wali Santri",
        "No WhatsApp Wali",
        "Username (Opsional)",
        "Password (Opsional)",
        "PIN 6 Digit (Opsional)"
    ]
    ws.append(headers)

    navy_fill = PatternFill(start_color="0A192F", end_color="0A192F", fill_type="solid")
    white_font = Font(name="Poppins", size=10, bold=True, color="FFFFFF")

    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill = navy_fill
        cell.font = white_font
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[openpyxl.utils.get_column_letter(col_num)].width = 24

    ws.append([
        "Ahmad Fauzan Al-Farisi", "Asrama Abu Bakar 101", "Kelas 11 IPA 1",
        "Bpk. H. Sulaiman", "+6281234567001", "ahmad", "santri123", "112233"
    ])
    ws.append([
        "Muhammad Rizky Pratama", "Asrama Umar 204", "Kelas 10 Agama 2",
        "Bpk. Hendra Wijaya", "+6281234567002", "", "santri123", ""
    ])

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=Template_Import_Santri_SIM_Pontren.xlsx"}
    )


@app.post("/api/students/import-excel")
def import_students_from_excel(req: ExcelImportRequest, db: Session = Depends(get_db)):
    try:
        b64_data = req.file_base64.split(",")[-1] if "," in req.file_base64 else req.file_base64
        file_bytes = base64.b64decode(b64_data)
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        ws = wb.active
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"File Excel tidak valid atau rusak: {str(e)}")

    imported_count = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue

        name = str(row[0]).strip()
        if not name:
            continue

        dorm_room = str(row[1]).strip() if len(row) > 1 and row[1] else "Asrama Utama"
        classroom = str(row[2]).strip() if len(row) > 2 and row[2] else "Kelas Reguler"
        parent_name = str(row[3]).strip() if len(row) > 3 and row[3] else "Wali Santri"
        parent_wa = str(row[4]).strip() if len(row) > 4 and row[4] else "+628000000000"
        custom_user = str(row[5]).strip() if len(row) > 5 and row[5] else None
        custom_pass = str(row[6]).strip() if len(row) > 6 and row[6] else "santri123"
        custom_pin = str(row[7]).strip() if len(row) > 7 and row[7] else None

        register_single_student_helper(
            db=db,
            name=name,
            dorm_room=dorm_room,
            classroom=classroom,
            parent_name=parent_name,
            parent_wa=parent_wa,
            custom_username=custom_user,
            custom_password=custom_pass,
            custom_pin=custom_pin
        )
        imported_count += 1

    if imported_count == 0:
        raise HTTPException(status_code=400, detail="Tidak ditemukan baris data santri di dalam file Excel.")

    return {
        "message": f"Berhasil mengimpor {imported_count} data santri beserta pembuatan Akun Login & PIN otomatis!"
    }


@app.delete("/api/students/{student_id}")
def delete_student_data(student_id: int, db: Session = Depends(get_db)):
    student = db.query(Student).filter(Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Data santri tidak ditemukan.")

    student_name = student.name
    student_nis = student.nis

    db.query(UserAccount).filter(UserAccount.linked_student_id == student_id).delete()
    db.query(AttendanceLog).filter(AttendanceLog.student_id == student_id).delete()
    db.query(LeaveRequest).filter(LeaveRequest.student_id == student_id).delete()
    db.query(InventoryLoan).filter(InventoryLoan.student_id == student_id).delete()
    db.query(BKEarlyWarningRecord).filter(BKEarlyWarningRecord.student_id == student_id).delete()
    db.delete(student)
    db.commit()

    return {"message": f"Data santri {student_name} ({student_nis}) beserta akun & riwayatnya berhasil dihapus."}


# ==========================================
# 9. ENDPOINT AI EARLY WARNING (BK MANUAL)
# ==========================================
@app.post("/api/bk-records/create")
def create_manual_bk_record(req: BKManualCreateRequest, db: Session = Depends(get_db)):
    student = db.query(Student).filter(Student.id == req.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Pilih santri terlebih dahulu.")

    if req.custom_risk_score is not None:
        risk_score = min(max(round(float(req.custom_risk_score), 1), 0.0), 100.0)
    else:
        calc = (req.late_subuh_curfew * 6.0) + (req.sakit_freq * 4.5) + (req.alfa_count * 15.0)
        risk_score = min(round(calc, 1), 100.0)

    if req.level and req.level.strip() != "AUTO":
        level_str = req.level.strip()
    else:
        if risk_score >= 70:
            level_str = "KRITIS (Panggil Konseling BK & Wali)"
        elif risk_score >= 40:
            level_str = "WASPADA (Pembinaan Musyrif)"
        else:
            level_str = "AMAN (Stabil)"

    if req.indicators and req.indicators.strip():
        indicators_text = req.indicators.strip()
    else:
        parts = []
        if req.late_subuh_curfew > 0:
            parts.append(f"Keterlambatan Subuh/Curfew {req.late_subuh_curfew}x")
        if req.sakit_freq > 0:
            parts.append(f"Frekuensi sakit Poskestren {req.sakit_freq}x")
        if req.alfa_count > 0:
            parts.append(f"Alfa / Mangkir kegiatan {req.alfa_count}x")
        indicators_text = " | ".join(parts) if parts else "Catatan evaluasi adab & kedisiplinan rutin"

    if req.recommendation and req.recommendation.strip():
        rec_text = req.recommendation.strip()
    else:
        if risk_score >= 70:
            rec_text = "Wajib konseling tatap muka dengan Musyrif & panggilan Wali Santri."
        elif risk_score >= 40:
            rec_text = "Pendekatan persuasif di kamar asrama & evaluasi jam tidur malam."
        else:
            rec_text = "Pertahankan pembinaan dan pendampingan rutin."

    record = BKEarlyWarningRecord(
        student_id=student.id,
        nis=student.nis,
        student_name=student.name,
        dorm_room=student.dorm_room,
        backup_pin=student.backup_pin,
        late_subuh_curfew=req.late_subuh_curfew,
        sakit_freq=req.sakit_freq,
        alfa_count=req.alfa_count,
        risk_score=risk_score,
        level=level_str,
        indicators=indicators_text,
        recommendation=rec_text
    )
    db.add(record)
    db.commit()

    return {"message": f"Catatan BK Early Warning untuk {student.name} berhasil ditambahkan secara manual."}


@app.delete("/api/bk-records/{record_id}")
def delete_single_bk_record(record_id: int, db: Session = Depends(get_db)):
    rec = db.query(BKEarlyWarningRecord).filter(BKEarlyWarningRecord.id == record_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Catatan BK tidak ditemukan.")
    db.delete(rec)
    db.commit()
    return {"message": "Riwayat catatan BK berhasil dihapus."}


@app.post("/api/bk-records/clear")
def clear_all_bk_records(db: Session = Depends(get_db)):
    db.query(BKEarlyWarningRecord).delete()
    db.commit()
    return {"message": "Seluruh riwayat AI Predictive Early Warning (BK) telah dikosongkan."}


# ==========================================
# 10. ENDPOINT PIN, RESET, & OPERASIONAL
# ==========================================
@app.post("/api/students/update-pin")
async def update_or_generate_student_pin(req: StudentPinUpdateRequest, db: Session = Depends(get_db)):
    if req.actor_role.upper() not in ("ADMIN", "USTADZ"):
        raise HTTPException(status_code=403, detail="Hanya Admin atau Ustadz yang berhak mengatur PIN Santri.")

    student = db.query(Student).filter(Student.id == req.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Data santri tidak ditemukan.")

    clean_pin = req.new_pin.strip()
    if not (clean_pin.isdigit() and len(clean_pin) == 6):
        raise HTTPException(status_code=400, detail="PIN Santri wajib terdiri dari 6 digit angka.")

    duplicate = db.query(Student).filter(
        Student.backup_pin == clean_pin,
        Student.id != student.id
    ).first()
    if duplicate:
        raise HTTPException(status_code=400, detail=f"PIN {clean_pin} sudah digunakan santri lain ({duplicate.name}).")

    old_pin = student.backup_pin
    student.backup_pin = clean_pin
    student.pin_usage_14d = 0

    for bk_rec in db.query(BKEarlyWarningRecord).filter(BKEarlyWarningRecord.student_id == student.id).all():
        bk_rec.backup_pin = clean_pin

    db.commit()

    wa_msg = (
        f"🔐 *PEMBARUAN PIN CADANGAN (BACKUP PIN) SANTRI*\n"
        f"Assalamu'alaikum Wr. Wb. Yth. *{student.parent_name}*,\n\n"
        f"PIN Cadangan presensi untuk Ananda *{student.name}* ({student.nis}) telah diperbarui oleh "
        f"*{req.actor_name} ({req.actor_role})*:\n"
        f"• *PIN Lama* : {old_pin}\n"
        f"• *PIN Baru (6-Digit)* : *{clean_pin}*"
    )
    await dispatch_whatsapp_notification(db, student, wa_msg)

    return {
        "message": f"PIN Santri {student.name} berhasil diatur menjadi {clean_pin} oleh {req.actor_role}."
    }


@app.post("/api/system/reset-all-data")
def reset_all_data_to_empty(db: Session = Depends(get_db)):
    db.query(AttendanceLog).delete()
    db.query(LeaveRequest).delete()
    db.query(InventoryLoan).delete()
    db.query(WhatsAppOutbox).delete()
    db.query(BKEarlyWarningRecord).delete()
    db.query(Student).delete()
    db.query(ShiftSchedule).delete()
    db.query(UserAccount).filter(UserAccount.role != "ADMIN").delete()
    db.commit()
    return {"message": "Seluruh data berhasil dikosongkan! Silakan mulai input data baru dari awal."}


@app.get("/", response_class=HTMLResponse)
def render_dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/state")
def get_system_state(db: Session = Depends(get_db)):
    students = db.query(Student).order_by(Student.id.asc()).all()
    shifts = db.query(ShiftSchedule).order_by(ShiftSchedule.id.asc()).all()
    logs = db.query(AttendanceLog).order_by(AttendanceLog.id.desc()).limit(50).all()
    leaves = db.query(LeaveRequest).order_by(LeaveRequest.id.desc()).all()
    loans = db.query(InventoryLoan).order_by(InventoryLoan.id.desc()).all()
    wa_logs = db.query(WhatsAppOutbox).order_by(WhatsAppOutbox.id.desc()).limit(25).all()
    accounts = db.query(UserAccount).order_by(UserAccount.id.asc()).all()
    bk_records = db.query(BKEarlyWarningRecord).order_by(BKEarlyWarningRecord.id.desc()).all()

    dorm_photo_setting = db.query(SystemSetting).filter(SystemSetting.key == "dorm_photo_url").first()
    dorm_photo_url = dorm_photo_setting.value if dorm_photo_setting else DEFAULT_DORM_PHOTO_URL

    missing_students = [
        {
            "id": s.id, "nis": s.nis, "name": s.name,
            "dorm_room": s.dorm_room, "consecutive_absent": s.consecutive_absent,
            "parent_wa": s.parent_wa
        }
        for s in students if s.consecutive_absent >= 3
    ]

    return {
        "dorm_photo_url": dorm_photo_url,
        "accounts": [
            {
                "id": a.id, "username": a.username, "password": a.password,
                "full_name": a.full_name, "role": a.role,
                "role_title": a.role_title, "linked_student_id": a.linked_student_id
            } for a in accounts
        ],
        "students": [
            {
                "id": s.id, "nis": s.nis, "name": s.name, "dorm_room": s.dorm_room,
                "classroom": s.classroom, "fingerprint_id": s.fingerprint_id,
                "backup_pin": s.backup_pin, "parent_name": s.parent_name,
                "parent_wa": s.parent_wa, "discipline_points": s.discipline_points,
                "consecutive_absent": s.consecutive_absent
            } for s in students
        ],
        "shifts": [
            {
                "id": sh.id, "name": sh.name, "category": sh.category,
                "start_time": sh.start_time, "on_time_limit": sh.on_time_limit,
                "end_time": sh.end_time, "late_points": sh.late_points,
                "absent_points": sh.absent_points
            } for sh in shifts
        ],
        "logs": [
            {
                "id": lg.id, "student_name": lg.student_name, "shift_name": lg.shift_name,
                "timestamp": lg.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "method": lg.method, "status": lg.status,
                "was_offline": lg.was_offline, "notes": lg.notes
            } for lg in logs
        ],
        "leaves": [
            {
                "id": lv.id, "student_name": lv.student_name, "leave_type": lv.leave_type,
                "reason": lv.reason, "start_date": lv.start_date, "end_date": lv.end_date,
                "approved_by": lv.approved_by, "status": lv.status
            } for lv in leaves
        ],
        "loans": [
            {
                "id": ln.id, "item_code": ln.item_code, "item_name": ln.item_name,
                "location": ln.location, "student_name": ln.student_name,
                "borrowed_at": ln.borrowed_at.strftime("%Y-%m-%d %H:%M"),
                "returned_at": ln.returned_at.strftime("%Y-%m-%d %H:%M") if ln.returned_at else "-",
                "status": ln.status
            } for ln in loans
        ],
        "wa_logs": [
            {
                "id": w.id, "parent_wa": w.parent_wa, "student_name": w.student_name,
                "message": w.message, "created_at": w.created_at.strftime("%H:%M:%S"),
                "delivery_status": w.delivery_status
            } for w in wa_logs
        ],
        "bk_records": [
            {
                "id": r.id,
                "student_id": r.student_id,
                "nis": r.nis,
                "name": r.student_name,
                "dorm_room": r.dorm_room,
                "backup_pin": r.backup_pin,
                "late_subuh_curfew": r.late_subuh_curfew,
                "sakit_freq": r.sakit_freq,
                "alfa_count": r.alfa_count,
                "risk_score": r.risk_score,
                "level": r.level,
                "indicators": r.indicators,
                "recommendation": r.recommendation,
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M")
            } for r in bk_records
        ],
        "missing_students": missing_students,
        "analytics": build_monthly_analytics(db)
    }


@app.post("/api/attendance/scan")
async def process_attendance_scan(req: ScanRequest, db: Session = Depends(get_db)):
    student = db.query(Student).filter(
        (Student.nis == req.identifier) |
        (Student.fingerprint_id == req.identifier) |
        (Student.backup_pin == req.identifier)
    ).first()
    if not student:
        raise HTTPException(status_code=404, detail="Data Sidik Jari / NIS / PIN Santri tidak ditemukan.")

    shift = db.query(ShiftSchedule).filter(ShiftSchedule.id == req.shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Shift kegiatan belum dibuat atau tidak ditemukan.")

    if req.auth_mode == "DUAL_VERIFICATION":
        if req.pin_code != student.backup_pin:
            raise HTTPException(status_code=401, detail="Dual-Verification Gagal: PIN pendamping sidik jari salah!")
    elif req.auth_mode == "PIN_BACKUP":
        if req.pin_code != student.backup_pin and req.identifier != student.backup_pin:
            raise HTTPException(status_code=401, detail="PIN Backup tidak valid!")
        student.pin_usage_14d += 1

    today_prefix = date.today().strftime("%Y-%m-%d")
    existing_logs = db.query(AttendanceLog).filter(
        AttendanceLog.student_id == student.id,
        AttendanceLog.shift_id == shift.id
    ).all()
    for log in existing_logs:
        if log.timestamp.strftime("%Y-%m-%d") == today_prefix:
            raise HTTPException(
                status_code=409,
                detail=f"ANTI-PASSBACK AKTIF: {student.name} sudah melakukan presensi pada shift '{shift.name}' hari ini."
            )

    now_dt = datetime.now()
    current_hhmm = req.scan_time_str if req.scan_time_str else now_dt.strftime("%H:%M")

    if current_hhmm <= shift.on_time_limit:
        status = "TEPAT_WAKTU"
        points_added = 0
    else:
        status = "TERLAMBAT"
        points_added = shift.late_points
        student.discipline_points += points_added
        student.late_count_14d += 1

    student.consecutive_absent = 0

    new_log = AttendanceLog(
        student_id=student.id,
        student_name=student.name,
        shift_id=shift.id,
        shift_name=shift.name,
        timestamp=now_dt,
        method=req.auth_mode,
        status=status,
        was_offline=req.was_offline,
        notes=req.notes or f"Scan jam {current_hhmm}"
    )
    db.add(new_log)
    db.commit()

    if WA_MODULE_AVAILABLE:
        wa_text = PesantrenWATemplates.attendance_checkin(
            parent_name=student.parent_name, student_name=student.name, nis=student.nis,
            dorm_room=student.dorm_room, shift_name=shift.name, scan_time=current_hhmm,
            status=status, auth_mode=req.auth_mode, points_added=points_added,
            total_points=student.discipline_points, was_offline=req.was_offline
        )
    else:
        wa_text = (
            f"Assalamu'alaikum {student.parent_name}, Ananda *{student.name}* telah presensi "
            f"*{shift.name}* pukul {current_hhmm} WIB ({status}). Total Poin: {student.discipline_points}."
        )
    await dispatch_whatsapp_notification(db, student, wa_text)

    return {
        "message": f"Presensi {student.name} berhasil dicatat ({status}).",
        "status": status,
        "points_added": points_added
    }


@app.post("/api/attendance/sync-offline")
async def sync_offline_records(batch: OfflineBatchRequest, db: Session = Depends(get_db)):
    synced = 0
    skipped_apb = 0
    for item in batch.records:
        item.was_offline = True
        try:
            await process_attendance_scan(item, db)
            synced += 1
        except HTTPException:
            skipped_apb += 1
    return {
        "message": f"Sinkronisasi selesai: {synced} data lokal berhasil diunggah, {skipped_apb} duplikat ditolak Anti-Passback."
    }


@app.post("/api/attendance/mark-alpha")
async def mark_student_unexcused(req: UnexcusedSweepRequest, db: Session = Depends(get_db)):
    student = db.query(Student).filter(Student.id == req.student_id).first()
    shift = db.query(ShiftSchedule).filter(ShiftSchedule.id == req.shift_id).first()
    if not student or not shift:
        raise HTTPException(status_code=404, detail="Santri atau Shift tidak ditemukan.")

    active_leave = db.query(LeaveRequest).filter(
        LeaveRequest.student_id == student.id,
        LeaveRequest.status == "AKTIF"
    ).first()
    if active_leave:
        raise HTTPException(
            status_code=400,
            detail=f"{student.name} sedang memiliki izin aktif ({active_leave.leave_type}). Tidak dikenakan poin Alfa."
        )

    student.discipline_points += shift.absent_points
    student.consecutive_absent += 1

    log = AttendanceLog(
        student_id=student.id,
        student_name=student.name,
        shift_id=shift.id,
        shift_name=shift.name,
        method="AUTO_SWEEP",
        status="ALFA",
        notes=f"Tidak hadir tanpa keterangan (+{shift.absent_points} Poin)"
    )
    db.add(log)
    db.commit()

    if WA_MODULE_AVAILABLE:
        if student.consecutive_absent >= 3:
            wa_msg = PesantrenWATemplates.missing_student_emergency(
                parent_name=student.parent_name, student_name=student.name, nis=student.nis,
                dorm_room=student.dorm_room, consecutive_count=student.consecutive_absent, last_shift=shift.name
            )
        else:
            wa_msg = PesantrenWATemplates.unexcused_alpha(
                parent_name=student.parent_name, student_name=student.name, dorm_room=student.dorm_room,
                shift_name=shift.name, points_added=shift.absent_points, total_points=student.discipline_points
            )
    else:
        wa_msg = f"Assalamu'alaikum {student.parent_name}, Ananda *{student.name}* tercatat ALFA pada {shift.name}."

    await dispatch_whatsapp_notification(db, student, wa_msg)
    return {"message": f"Status ALFA tercatat untuk {student.name}. Absen beruntun: {student.consecutive_absent}x."}


@app.post("/api/leaves")
async def create_leave_permit(req: LeaveCreateRequest, db: Session = Depends(get_db)):
    student = db.query(Student).filter(Student.id == req.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan.")

    if req.leave_type == "SAKIT":
        student.sakit_count_14d += 1

    leave = LeaveRequest(
        student_id=student.id,
        student_name=student.name,
        leave_type=req.leave_type,
        reason=req.reason,
        start_date=req.start_date,
        end_date=req.end_date,
        approved_by=req.approved_by
    )
    db.add(leave)
    db.commit()

    if WA_MODULE_AVAILABLE:
        wa_msg = PesantrenWATemplates.leave_or_medical_permit(
            parent_name=student.parent_name, student_name=student.name, dorm_room=student.dorm_room,
            leave_type=req.leave_type, start_date=req.start_date, end_date=req.end_date,
            reason=req.reason, approved_by=req.approved_by
        )
    else:
        wa_msg = f"Assalamu'alaikum {student.parent_name}, izin {req.leave_type} untuk {student.name} telah disetujui."

    await dispatch_whatsapp_notification(db, student, wa_msg)
    return {"message": "Surat izin santri berhasil disimpan & dikirim ke WhatsApp Wali."}


@app.post("/api/inventory/loan")
def create_inventory_loan(req: InventoryCreateRequest, db: Session = Depends(get_db)):
    student = db.query(Student).filter(Student.id == req.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Santri tidak ditemukan.")

    loan = InventoryLoan(
        item_code=req.item_code,
        item_name=req.item_name,
        location=req.location,
        student_id=student.id,
        student_name=student.name
    )
    db.add(loan)
    db.commit()
    return {"message": f"Peminjaman {req.item_name} oleh {student.name} berhasil dicatat."}


@app.post("/api/inventory/return/{loan_id}")
def return_inventory_loan(loan_id: int, db: Session = Depends(get_db)):
    loan = db.query(InventoryLoan).filter(InventoryLoan.id == loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Data peminjaman tidak ditemukan.")
    loan.status = "DIKEMBALIKAN"
    loan.returned_at = datetime.now()
    db.commit()
    return {"message": f"Inventaris {loan.item_name} telah dikembalikan."}


@app.get("/api/reports/export-excel")
def export_attendance_excel(db: Session = Depends(get_db)):
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Rekap Kehadiran"
    headers1 = ["ID Log", "Nama Santri", "Kegiatan / Shift", "Waktu Scan", "Metode Verifikasi", "Status", "Sync Offline", "Catatan"]
    ws1.append(headers1)

    navy_fill = PatternFill(start_color="0A192F", end_color="0A192F", fill_type="solid")
    white_font = Font(name="Poppins", size=10, bold=True, color="FFFFFF")

    for col_num in range(1, len(headers1) + 1):
        cell = ws1.cell(row=1, column=col_num)
        cell.fill = navy_fill
        cell.font = white_font
        cell.alignment = Alignment(horizontal="center")

    logs = db.query(AttendanceLog).order_by(AttendanceLog.id.desc()).all()
    for lg in logs:
        ws1.append([
            lg.id, lg.student_name, lg.shift_name,
            lg.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            lg.method, lg.status,
            "Ya" if lg.was_offline else "Tidak", lg.notes
        ])

    ws2 = wb.create_sheet(title="Catatan Manual AI BK")
    headers2 = ["Waktu Input", "NIS", "Nama Santri", "Asrama", "PIN Backup", "Skor Risiko", "Status Peringatan", "Indikator Pola Perilaku", "Rekomendasi BK"]
    ws2.append(headers2)
    for col_num in range(1, len(headers2) + 1):
        cell = ws2.cell(row=1, column=col_num)
        cell.fill = PatternFill(start_color="059669", end_color="059669", fill_type="solid")
        cell.font = white_font

    for r in db.query(BKEarlyWarningRecord).order_by(BKEarlyWarningRecord.id.desc()).all():
        ws2.append([
            r.created_at.strftime("%Y-%m-%d %H:%M"), r.nis, r.student_name,
            r.dorm_room, r.backup_pin, r.risk_score, r.level, r.indicators, r.recommendation
        ])

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    filename = f"Laporan_SIM_Pontren_{date.today()}.xlsx"
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)