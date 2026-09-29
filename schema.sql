-- ============================================================
-- 初中数学讲义自动化生成系统 - 数据库建表 SQL
-- 用法: sqlite3 questions.db < schema.sql
-- ============================================================

PRAGMA foreign_keys = ON;

-- 题目表
CREATE TABLE IF NOT EXISTS questions (
    id              TEXT PRIMARY KEY,          -- UUID
    subject         TEXT NOT NULL,              -- 题干（含 LaTeX）
    answer          TEXT,                       -- 答案
    knowledge_point TEXT,                       -- 知识点（如"有理数加法"）
    sub_knowledge   TEXT,                       -- 子知识点
    difficulty      TEXT,                       -- 基础 / 中等 / 高 / 拓展
    error_prone     TEXT,                       -- 易错点标签
    grade_level     TEXT,                       -- 学段（七上/八下/高一上/小升初/初中等，见 textbook_filter.ALL_GRADE_LEVELS）
    stage           TEXT,                       -- 大学段（小学/初中/高中），自动从 grade_level 推导
    question_type   TEXT,                       -- 题型（选择 / 填空 / 计算 / 证明 / 应用）
    options         TEXT,                       -- 选择题选项（JSON 数组）
    has_image       INTEGER DEFAULT 0,          -- 是否含图
    source_file     TEXT,                       -- 来源文件名
    source_page     INTEGER,                    -- 来源页码
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 入库时间
    used_count      INTEGER DEFAULT 0           -- 被选用次数（用于去重优先级）
);

-- 图片表
CREATE TABLE IF NOT EXISTS images (
    id          TEXT PRIMARY KEY,               -- UUID
    question_id TEXT NOT NULL,                  -- 关联题目
    filename    TEXT,                           -- 图片文件名
    filepath    TEXT,                           -- 图片存储路径
    page_num    INTEGER,                        -- 在原文件中的页码
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

-- 知识点标签表（支持层级）
CREATE TABLE IF NOT EXISTS knowledge_tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,           -- 标签名
    parent_id   INTEGER,                        -- 父标签ID（支持层级）
    grade_level TEXT,                           -- 所属学段
    FOREIGN KEY (parent_id) REFERENCES knowledge_tags(id) ON DELETE SET NULL
);

-- 讲义生成记录表
CREATE TABLE IF NOT EXISTS lecture_sessions (
    id           TEXT PRIMARY KEY,              -- UUID
    grade_level  TEXT,                          -- 学段
    day_number   INTEGER,                       -- 第几天
    topic        TEXT,                          -- 主题
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 生成时间
    config_json  TEXT                           -- 生成配置（JSON）
);

-- 讲义-题目关联表
CREATE TABLE IF NOT EXISTS session_questions (
    session_id  TEXT NOT NULL,                  -- 讲义ID
    question_id TEXT NOT NULL,                  -- 题目ID
    section     TEXT,                           -- 所属环节（诊断测试 / 基础必做 / 分层拓展等）
    layer       TEXT,                           -- 分层（A/B/C，仅分层拓展层有值）
    sort_order  INTEGER,                        -- 排序
    PRIMARY KEY (session_id, question_id),
    FOREIGN KEY (session_id) REFERENCES lecture_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_questions_knowledge_point ON questions(knowledge_point);
CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions(difficulty);
CREATE INDEX IF NOT EXISTS idx_questions_grade_level ON questions(grade_level);
CREATE INDEX IF NOT EXISTS idx_questions_stage ON questions(stage);
CREATE INDEX IF NOT EXISTS idx_questions_used_count ON questions(used_count);
CREATE INDEX IF NOT EXISTS idx_images_question_id ON images(question_id);
CREATE INDEX IF NOT EXISTS idx_session_questions_session ON session_questions(session_id);
