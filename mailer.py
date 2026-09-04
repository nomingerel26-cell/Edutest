# -*- coding: utf-8 -*-
"""
Имэйл илгээх — стандарт сангийн `smtplib`, `email` дээр.

Шинэ dependency НЭМЭЭГҮЙ: төслийн бусад хэсэг адил зөвхөн стандарт сан.

Тохиргоо орчны хувьсагчаар (аль нэг нь дутвал илгээх боломж унтарна):
    EDUTEST_SMTP_HOST      жишээ: smtp.gmail.com
    EDUTEST_SMTP_PORT      587 (STARTTLS) эсвэл 465 (SSL). Анхдагч 587
    EDUTEST_SMTP_USER      нэвтрэх нэр (ихэвчлэн имэйл хаяг)
    EDUTEST_SMTP_PASSWORD  нууц үг. Gmail бол App Password ЗААВАЛ —
                           энгийн нууц үг ажиллахгүй
    EDUTEST_SMTP_FROM      илгээгчийн хаяг. Дутвал USER-ийг хэрэглэнэ
    EDUTEST_SMTP_TLS       'ssl' бол шууд SSL (465), эс бөгөөс STARTTLS

Нууц үгийг ХЭЗЭЭ Ч лог, алдааны мессежид гаргахгүй.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage

# SMTP сервер хариу өгөхгүй бол gunicorn-ы worker дүүжлэгдэнэ (--timeout 120).
# Түүнээс нэлээд бага утга авч, хүсэлт цагтаа дуусахыг баталгаажуулна.
TIMEOUT_SECONDS = 20

REQUIRED = ("EDUTEST_SMTP_HOST", "EDUTEST_SMTP_USER", "EDUTEST_SMTP_PASSWORD")


class MailError(Exception):
    """Илгээлт бүтэлгүйтэв. Мессеж нь хэрэглэгчид харуулахад тохиромжтой."""


def missing_settings() -> list:
    """Тохируулаагүй хувьсагчдын нэр. Хоосон бол илгээхэд бэлэн."""
    return [name for name in REQUIRED if not (os.environ.get(name) or "").strip()]


def is_configured() -> bool:
    return not missing_settings()


def sender_address() -> str:
    return ((os.environ.get("EDUTEST_SMTP_FROM") or "").strip()
            or (os.environ.get("EDUTEST_SMTP_USER") or "").strip())


def send(to: str, subject: str, body: str, attachments=None) -> None:
    """Нэг имэйл илгээнэ.

    attachments: [(файлын нэр, bytes, mime төрөл), ...]

    Алдаа гарвал MailError — мессеж нь шууд хэрэглэгчид харуулахад
    тохиромжтой, нууц үг агуулахгүй.
    """
    missing = missing_settings()
    if missing:
        raise MailError("Имэйлийн тохиргоо дутуу байна: " + ", ".join(missing))
    if not (to or "").strip():
        raise MailError("Хүлээн авагчийн хаяг хоосон байна.")

    host = os.environ["EDUTEST_SMTP_HOST"].strip()
    user = os.environ["EDUTEST_SMTP_USER"].strip()
    password = os.environ["EDUTEST_SMTP_PASSWORD"]
    use_ssl = (os.environ.get("EDUTEST_SMTP_TLS") or "").strip().lower() == "ssl"
    port = int((os.environ.get("EDUTEST_SMTP_PORT") or "").strip()
               or (465 if use_ssl else 587))

    message = EmailMessage()
    message["From"] = sender_address()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    for filename, data, mimetype in (attachments or []):
        maintype, _, subtype = mimetype.partition("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype,
                               filename=filename)

    context = ssl.create_default_context()
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS,
                                  context=context) as server:
                server.login(user, password)
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS) as server:
                server.starttls(context=context)
                server.login(user, password)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        # Gmail-ийн хамгийн түгээмэл алдаа. Хариунд нууц үг ОРОХГҮЙ.
        raise MailError(
            f"Нэвтрэлт амжилтгүй ({exc.smtp_code}). Gmail бол энгийн нууц үг "
            f"биш, App Password хэрэглэнэ."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Илгээх үед алдаа гарлаа: {type(exc).__name__}") from exc
