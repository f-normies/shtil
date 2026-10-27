"""Учётные записи нагрузочного теста (#41). Без импорта Locust: его подключает и prepare.py."""

import os

PASSWORD = os.environ["LOADTEST_PASSWORD"]
DOMAIN = "loadtest.shtil.ru"
DOCTORS = int(os.environ.get("LOADTEST_DOCTORS", "50"))
PATIENTS_PER_DOCTOR = int(os.environ.get("LOADTEST_PATIENTS_PER_DOCTOR", "20"))
PROBE_DOCTOR = f"lt-probe-doctor@{DOMAIN}"
PROBE_PATIENT = f"lt-probe-patient@{DOMAIN}"


def doctor_email(n: int) -> str:
    """Врач номер `n`, с 1."""
    return f"lt-doctor-{n:02d}@{DOMAIN}"


def patient_email(doctor: int, n: int) -> str:
    """Пациент номер `n` врача `doctor`, оба с 1."""
    return f"lt-p{doctor:02d}-{n:02d}@{DOMAIN}"
