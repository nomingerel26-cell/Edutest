-- =====================================================================
-- EduTest — SQLite схем (MVP)
--
-- POSTGRESQL-Д ШИЛЖИХ ХИЛ (migration boundary):
--   Энэ файл бол SQLite-ийн аялгуугаар (dialect) бичигдсэн ЦОРЫН ГАНЦ DDL.
--   PostgreSQL руу шилжихэд зөвхөн ЭНЭ файлыг schema_postgres.sql болгон
--   хөрвүүлнэ. Хөрвүүлэх дүрэм:
--     INTEGER PRIMARY KEY AUTOINCREMENT  ->  BIGSERIAL PRIMARY KEY
--     TEXT (огноо)                       ->  TIMESTAMPTZ
--     INTEGER (0/1 туг)                  ->  BOOLEAN
--     PRAGMA foreign_keys                ->  Postgres-д шаардлагагүй (үргэлж идэвхтэй)
--   Хүснэгт, багана, CHECK, UNIQUE, FOREIGN KEY-ийн НЭР бүгд адилхан хэвээр
--   үлдэх тул database.py доторх SQL query-үүд өөрчлөгдөхгүй.
-- =====================================================================

PRAGMA foreign_keys = ON;

-- Багш / админ хэрэглэгчид -------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,               -- ХЭЗЭЭ Ч ил задгай нууц үг хадгалахгүй
    role          TEXT NOT NULL CHECK (role IN ('admin', 'teacher')),
    department    TEXT,
    created_at    TEXT NOT NULL
);

-- Хичээлүүд ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS courses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    code       TEXT NOT NULL UNIQUE,
    -- REAL: 3, 3.5 зэрэг бутархай кредит зөвшөөрнө.
    -- SQLite динамик төрөлтэй тул хуучин INTEGER өгөгдөл хэвээр уншигдана.
    credit     REAL NOT NULL DEFAULT 3,
    semester   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Ангийн группүүд ------------------------------------------------------
CREATE TABLE IF NOT EXISTS class_groups (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id     INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    student_count INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    UNIQUE (course_id, name)
);

-- Pre/Post тестийн хос -------------------------------------------------
CREATE TABLE IF NOT EXISTS test_pairs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Тестүүд --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id      INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    pair_id        INTEGER REFERENCES test_pairs(id) ON DELETE SET NULL,
    class_group_id INTEGER REFERENCES class_groups(id) ON DELETE SET NULL,
    title          TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('pre', 'post')),
    status         TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'open', 'closed')),
    -- Хэн тест өгөх эрхтэй вэ:
    --   'any'    — хэн ч ямар ч оюутны код бичээд орно (гадны сургалт,
    --              семинар, кодгүй оролцогчид).
    --   'roster' — багшийн урьдчилан бүртгэсэн кодоор л орно.
    -- CHECK тавихгүй: миграцаар нэмэгдэх багана хязгаарлалтгүй байдаг тул
    -- шинэ ба хуучин сан зөрөхөөс сэргийлж утгыг Python талд шалгана
    -- (domain.ENTRY_MODES).
    entry_mode     TEXT NOT NULL DEFAULT 'any',
    share_code     TEXT NOT NULL UNIQUE,
    created_at     TEXT NOT NULL
);

-- Асуултууд — гурван төрөл: single / multi / match ---------------------
--
-- qtype = 'single'  Нэг зөв хариулт.
--                   correct_option = 'A'
--                   match_a..match_d ашиглагдахгүй (NULL).
--
-- qtype = 'multi'   Олон зөв хариулт.
--                   correct_option = 'A,C' (үргэлж A,B,C,D дарааллаар)
--
-- qtype = 'match'   Харгалзуулах. option_a..option_d нь ЗҮҮН талын зүйлс,
--                   match_a..match_d нь тэдгээрийн ЗӨВ хос (match_a нь
--                   option_a-гийн хос). Дэлгэц дээр баруун талыг
--                   domain.match_display_order()-оор сэлгэж харуулна тул
--                   зөв хариулт эгнээгээрээ таарахгүй.
--                   correct_option ашиглагдахгүй ('').
--
-- correct_option дээр CHECK ТАВИААГҮЙ: 'A,C' болон '' зэрэг утга орно.
-- Утга бүрийн зөв эсэхийг domain.validate_question шалгана.
CREATE TABLE IF NOT EXISTS questions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id        INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    order_no       INTEGER NOT NULL,
    text           TEXT NOT NULL,
    qtype          TEXT NOT NULL DEFAULT 'single'
                        CHECK (qtype IN ('single', 'multi', 'match')),
    option_a       TEXT NOT NULL,
    option_b       TEXT NOT NULL,
    option_c       TEXT NOT NULL,
    option_d       TEXT NOT NULL,
    -- 5 дахь сонголт. Нэг/олон сонголттой асуултад хэрэглэнэ; харгалзуулах
    -- нь 4 мөр хэвээр тул match_e багана байхгүй. DEFAULT '' — хуучин
    -- мөрүүд миграцаар нэмэгдэхэд утга шаардахгүй.
    option_e       TEXT NOT NULL DEFAULT '',
    match_a        TEXT,
    match_b        TEXT,
    match_c        TEXT,
    match_d        TEXT,
    correct_option TEXT NOT NULL DEFAULT '',
    score          INTEGER NOT NULL DEFAULT 1 CHECK (score > 0)
);

-- Оюутнууд (нэвтрэлтгүй, зөвхөн бүртгэлийн мэдээлэл) --------------------
-- Pre/Post ТААРУУЛАЛТЫН ТҮЛХҮҮР:
--     class_group_id + normalized_student_code
-- Имэйл нь тааруулалтад ХЭЗЭЭ Ч оролцохгүй — зөвхөн харуулах, заавал биш талбар
-- (оюутан Оролт/Гаралт дээр өөр имэйл бичсэн ч, огт бичээгүй ч тааруулалт ажиллана).
-- full_name нь харуулах ба зөрчил тулгах зориулалттай — түлхүүр БИШ.
CREATE TABLE IF NOT EXISTS students (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    class_group_id          INTEGER NOT NULL REFERENCES class_groups(id) ON DELETE CASCADE,
    full_name               TEXT NOT NULL,   -- анх бүртгүүлсэн нэр (харуулах)
    student_code            TEXT NOT NULL,   -- оюутны бичсэн эх хэлбэр (харуулах)
    normalized_student_code TEXT NOT NULL,   -- ЗӨВХӨН тааруулахад (trim + том үсэг + зайгүй)
    email                   TEXT,            -- ЗААВАЛ БИШ, зөвхөн харуулах
    created_at              TEXT NOT NULL,
    -- ШААРДЛАГА: нэг группд нэг хэвийн болгосон оюутны код давхардахгүй.
    UNIQUE (class_group_id, normalized_student_code)
);

-- Тест бөглөх оролдлого -------------------------------------------------
CREATE TABLE IF NOT EXISTS attempts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id           INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    -- Тааруулалт ЗӨВХӨН нэг хосын дотор явагдана (өөр хосын оролдлого холилдохгүй).
    test_pair_id      INTEGER REFERENCES test_pairs(id) ON DELETE SET NULL,
    student_id        INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    -- 'grp:<class_group_id>|code:<NORMALIZED_STUDENT_CODE>'
    match_key         TEXT NOT NULL,
    -- Тухайн оролдлогод оюутны бичсэн нэр. students.full_name-ээс зөрвөл
    -- ЧИМЭЭГҮЙ НЭГТГЭХГҮЙ — багшид «нэрийн зөрчил» болгон тэмдэглэнэ.
    entered_full_name TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    submitted_at      TEXT,
    total_score       INTEGER,
    max_score         INTEGER,
    percent           INTEGER,
    UNIQUE (test_id, student_id)            -- нэг тестэд нэг оюутан нэг удаа
);

-- Хариултууд ------------------------------------------------------------
-- Хариултууд ------------------------------------------------------------
-- selected_option-ийн хэлбэр нь асуултын төрлөөс хамаарна:
--   single : 'A'
--   multi  : 'A,C'
--   match  : 'A>2,B>1,C>4,D>3'  (зүүн үсэг > дэлгэц дээрх мөрийн дугаар)
-- Тиймээс CHECK ТАВИААГҮЙ. Задлан шинжилгээг domain.py хийнэ.
CREATE TABLE IF NOT EXISTS answers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id      INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    question_id     INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    selected_option TEXT,
    is_correct      INTEGER NOT NULL DEFAULT 0,
    earned_score    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (attempt_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_questions_test    ON questions(test_id, order_no);
CREATE INDEX IF NOT EXISTS idx_tests_course      ON tests(course_id);
CREATE INDEX IF NOT EXISTS idx_attempts_test     ON attempts(test_id);
CREATE INDEX IF NOT EXISTS idx_attempts_match    ON attempts(match_key);
CREATE INDEX IF NOT EXISTS idx_attempts_pair     ON attempts(test_pair_id);
CREATE INDEX IF NOT EXISTS idx_students_match    ON students(class_group_id, normalized_student_code);
CREATE INDEX IF NOT EXISTS idx_answers_attempt   ON answers(attempt_id);
