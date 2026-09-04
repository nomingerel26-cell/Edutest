#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EduTest — өгөгдлийн сангийн нөөцлөлт.

Яагаад тусдаа скрипт вэ: ажиллаж байгаа SQLite файлыг ЗҮГЭЭР ХУУЛАХ нь
аюултай. Бичилтийн дундуур хуулбар авбал хагас гүйлгээ орж, эвдэрсэн сан
гарч ирнэ. WAL горимд бүр ноцтой — `-wal` файлд байгаа гүйлгээ үндсэн
файлд хараахан ороогүй байдаг тул зөвхөн үндсэн файлыг хуулах нь
өгөгдөл алдахад хүргэнэ.

Энд SQLite-ийн албан ёсны онлайн нөөцлөх API (`Connection.backup`)
ашиглана. Тэр нь түгжээг зөв зохицуулж, ямар ч мөчид БҮРЭН БҮТЭН
хуулбар гаргадаг — серверийг зогсоох шаардлагагүй.

Ажиллуулах:
    python3 backup.py                     # анхдагч: <сангийн хавтас>/backups
    python3 backup.py --out /data/backups --keep 30

Орчны хувьсагч:
    EDUTEST_DB          — эх сан (database.py-тэй ижил)
    EDUTEST_BACKUP_DIR  — хаана хадгалах
    EDUTEST_BACKUP_KEEP — хэдийг үлдээх (анхдагч 14)

Гаралт нь Dokploy-гийн Logs таб руу бичигдэнэ. Амжилтгүй бол exit code
0-ээс ялгаатай буцаана — cron/Schedules үүнийг алдаа гэж үзнэ.
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import database as db  # noqa: E402

DEFAULT_KEEP = 14


def _log(msg: str) -> None:
    """Шууд flush — контейнер богино хугацаанд унтрахад мессеж алдагдахгүй."""
    print(msg, flush=True)


def make_backup(source: Path, out_dir: Path) -> Path:
    """Бүрэн бүтэн хуулбар үүсгээд замыг нь буцаана."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"edutest-{stamp}.db"
    # Тэмдэглэгээ нь секундын нарийвчлалтай. Нэг секундэд хоёр удаа
    # ажиллуулбал (гараар туршихад амархан тохиолдоно) нэр давхцаж,
    # өмнөх нөөцлөлтийг дарж бичих байсан.
    counter = 2
    while target.exists():
        target = out_dir / f"edutest-{stamp}-{counter}.db"
        counter += 1

    # Түр нэрээр бичээд дуусмагц нь солино. Ингэснээр хагас бичигдсэн
    # файл хэзээ ч нөөцлөлт мэт харагдахгүй — rotation түүнийг сайн
    # хуулбар гэж андуурахгүй.
    partial = target.with_suffix(".db.partial")
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(partial))
        try:
            src.backup(dst)          # SQLite-ийн онлайн нөөцлөх API
        finally:
            dst.close()
    finally:
        src.close()
    partial.replace(target)
    return target


def verify(path: Path) -> int:
    """Хуулбар уншигдаж байгааг шалгаж, хэрэглэгчийн тоог буцаана.

    `integrity_check` нь эвдэрсэн файлыг илрүүлнэ. Нөөцлөлт нь сэргээх
    үед л хэрэгтэй болдог тул тэр үед гэмтэлтэй байсныг мэдэх нь оройтсон
    байдаг — ЭНД шалгана.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"integrity_check амжилтгүй: {result}")
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


def backup_dir() -> Path:
    """Нөөцлөлт хаана хадгалагдах вэ.

    `main()`-ийн анхдагчтай ИЖИЛ логик — хуваарьт ажил `--out`-гүй
    ажилласан ч, `/data/backups` гэж зааж ажилласан ч энэ хавтас руу
    ирнэ (EDUTEST_DB нь /data/... тул эцэг хавтас нь /data).
    """
    override = (os.environ.get("EDUTEST_BACKUP_DIR") or "").strip()
    if override:
        return Path(override)
    source = Path((os.environ.get("EDUTEST_DB") or "").strip()) if os.environ.get(
        "EDUTEST_DB") else db.DB_PATH
    return source.parent / "backups"


def list_backups(out_dir: Path | None = None) -> list:
    """Байгаа нөөцлөлтүүд, ШИНЭ нь эхэндээ.

    Хуваарьт нөөцлөлт ажилласан эсэхийг UI-аас харахад хэрэглэнэ.
    Хавтас байхгүй бол хоосон жагсаалт — алдаа биш, зүгээр л хараахан
    нэг ч нөөцлөлт хийгдээгүй гэсэн үг.
    """
    out_dir = out_dir or backup_dir()
    if not out_dir.is_dir():
        return []
    rows = []
    for path in out_dir.glob("edutest-*.db"):
        try:
            stat = path.stat()
        except OSError:
            continue        # зэрэг ажиллаж буй rotation устгасан байж болно
        rows.append({"name": path.name, "size_kb": round(stat.st_size / 1024, 1),
                     "mtime": stat.st_mtime})
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows


def rotate(out_dir: Path, keep: int) -> list:
    """Хамгийн сүүлийн `keep` ширхэгийг үлдээж, бусдыг устгана.

    ҮҮСГЭСЭН ЦАГААР эрэмбэлнэ, нэрээр БИШ. Нэр давхцахад нэмэгддэг
    `-2`, `-3` дагавар нь нэрийн эрэмбийг цаг хугацааны эрэмбээс
    салгадаг: ASCII-д '-' (0x2D) нь '.' (0x2E)-ээс өмнө ордог тул
    `edutest-...-2.db` нь `edutest-....db`-ээс өмнө эрэмбэлэгдэж,
    хамгийн хуучныг шинэ мэт үзэж БУРУУ файл устгах байсан.
    """
    backups = sorted(out_dir.glob("edutest-*.db"),
                     key=lambda p: (p.stat().st_mtime_ns, p.name))
    doomed = backups[:-keep] if keep > 0 and len(backups) > keep else []
    for path in doomed:
        path.unlink()
    return doomed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="EduTest-ийн сангийн нөөцлөлт")
    parser.add_argument("--source", default=os.environ.get("EDUTEST_DB"),
                        help="эх сан (анхдагч: EDUTEST_DB эсвэл edutest.db)")
    parser.add_argument("--out", default=os.environ.get("EDUTEST_BACKUP_DIR"),
                        help="нөөцлөлтийн хавтас (анхдагч: <сангийн хавтас>/backups)")
    parser.add_argument("--keep", type=int,
                        default=int(os.environ.get("EDUTEST_BACKUP_KEEP", DEFAULT_KEEP)),
                        help=f"хэдийг үлдээх (анхдагч {DEFAULT_KEEP})")
    args = parser.parse_args(argv)

    source = Path(args.source) if args.source else db.DB_PATH
    if not source.exists():
        _log(f"АЛДАА: эх сан олдсонгүй: {source}")
        return 1

    out_dir = Path(args.out) if args.out else source.parent / "backups"

    try:
        target = make_backup(source, out_dir)
        users = verify(target)
    except Exception as exc:            # noqa: BLE001 — cron-д тодорхой мессеж хэрэгтэй
        _log(f"АЛДАА: нөөцлөлт амжилтгүй: {exc!r}")
        return 1

    size_kb = target.stat().st_size / 1024
    _log(f"✓ Нөөцлөлт: {target}  ({size_kb:.1f} KB, {users} хэрэглэгч)")

    removed = rotate(out_dir, args.keep)
    if removed:
        _log(f"  хуучин {len(removed)} нөөцлөлт устлаа (сүүлийн {args.keep}-г үлдээв)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
