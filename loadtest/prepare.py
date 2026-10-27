"""Пациенты нагрузочного теста (#41) через API: приглашение врача и регистрация по нему.

Врачей заранее создаёт команда из loadtest/README.md: в API создания врача нет.
Повторный запуск пропускает пациентов, которые уже есть в списке врача. Существование
не проверяем входом или повторной регистрацией: неудачи с одного IP упираются в лимиты.

    python prepare.py --host https://<адрес>:8443 [--doctors 50] [--per-doctor 20]
"""

import argparse
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import requests
import urllib3
from accounts import (
    DOMAIN,
    PASSWORD,
    PROBE_DOCTOR,
    PROBE_PATIENT,
    doctor_email,
    patient_email,
)

log = logging.getLogger("prepare")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def login(host: str, email: str) -> requests.Session | None:
    session = requests.Session()
    session.verify = False
    resp = session.post(
        f"{host}/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return session if resp.status_code == 200 else None


def register(host: str, doctor: requests.Session, email: str, consent: str) -> str:
    invite = doctor.post(f"{host}/api/v1/patients/invites")
    invite.raise_for_status()
    resp = requests.post(
        f"{host}/api/v1/patients/register",
        verify=False,
        json={
            "invite_token": invite.json()["token"],
            "email": email,
            "password": PASSWORD,
            "full_name": f"Нагрузочный Пациент {email.split('@')[0][5:]}",
            "consent_personal_data": True,
            "consent_version": consent,
        },
    )
    if resp.status_code == 409:
        return "exists"
    resp.raise_for_status()
    return "created"


def prepare_doctor(host: str, n: int, per_doctor: int, consent: str) -> dict[str, int]:
    return prepare_patients(
        host,
        doctor_email(n),
        [patient_email(n, i) for i in range(1, per_doctor + 1)],
        consent,
    )


def prepare_patients(
    host: str, doctor_address: str, emails: list[str], consent: str
) -> dict[str, int]:
    """Зарегистрировать у врача пациентов из `emails`, которых ещё нет в его списке."""
    doctor = login(host, doctor_address)
    if doctor is None:
        raise SystemExit(
            f"{doctor_address} не входит: создайте врачей по loadtest/README.md"
        )
    listed = doctor.get(f"{host}/api/v1/patients")
    listed.raise_for_status()
    existing = {p["email"] for p in listed.json()}
    counts = {"created": 0, "exists": 0}
    for email in emails:
        if email in existing:
            counts["exists"] += 1
            continue
        counts[register(host, doctor, email, consent)] += 1
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host", default=os.environ.get("LOADTEST_HOST"), required=False
    )
    parser.add_argument("--doctors", type=int, default=50)
    parser.add_argument("--per-doctor", type=int, default=20)
    args = parser.parse_args()
    consent = requests.get(f"{args.host}/api/v1/patients/consent", verify=False).json()[
        "version"
    ]
    total = {"created": 0, "exists": 0}
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [
            pool.submit(prepare_doctor, args.host, n, args.per_doctor, consent)
            for n in range(1, args.doctors + 1)
        ]
        jobs.append(
            pool.submit(
                prepare_patients, args.host, PROBE_DOCTOR, [PROBE_PATIENT], consent
            )
        )
        for job in jobs:
            for key, value in job.result().items():
                total[key] += value
    log.info(
        "пациенты @%s: создано %s, уже были %s",
        DOMAIN,
        total["created"],
        total["exists"],
    )


if __name__ == "__main__":
    main()
