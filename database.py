"""
数据库管理模块 - QuestionBankDB

提供 SQLite 题目库的完整 CRUD 操作，支持：
- 建表 / 插入 / 去重 / 查询 / 使用计数 / 导出导入
"""

import json
import logging
import os
import sqlite3
import uuid
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "question_bank.db")

# SQL 文件路径
SCHEMA_SQL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

# 去重相似度阈值
SIMILARITY_THRESHOLD = 0.90

# 允许的难度值
VALID_DIFFICULTIES = {"基础", "中等", "高", "拓展"}

# 学段体系常量（从 textbook_filter 单一数据源导入）
from textbook_filter import ALL_GRADE_LEVELS, STAGE_MAP, EQUIVALENT_GRADES
VALID_GRADE_LEVELS = set(ALL_GRADE_LEVELS)


class QuestionBankDB:
    """题目库数据库管理类"""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        """初始化数据库连接。

        Args:
            db_path: SQLite 数据库文件路径
        """
        if not db_path:
            raise ValueError("db_path 不能为空")
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    # ---------- 连接管理 ----------

    def connect(self) -> sqlite3.Connection:
        """建立数据库连接并启用外键约束。"""
        if self.conn is not None:
            return self.conn
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        return self.conn

    def close(self):
        """关闭数据库连接。"""
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _ensure_connected(self) -> sqlite3.Connection:
        """确保连接存在，自动连接。"""
        if self.conn is None:
            self.connect()
        return self.conn

    # ---------- 初始化 ----------

    def init_db(self):
        """创建所有表和索引。若表已存在则跳过。"""
        conn = self._ensure_connected()
        try:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='questions'"
            )
            questions_exists = cursor.fetchone() is not None

            # 旧库先补 stage/options 列，避免 schema.sql 里的索引或字段在老表上报错
            if questions_exists:
                self._migrate_add_stage_column(conn)
                self._migrate_add_options_column(conn)

            # 读取 schema.sql 文件执行
            if os.path.exists(SCHEMA_SQL):
                with open(SCHEMA_SQL, "r", encoding="utf-8") as f:
                    sql = f.read()
                conn.executescript(sql)
            else:
                # 内联建表逻辑（兜底）
                self._create_tables_inline(conn)

            conn.commit()

            # 新库在 schema 创建完后，补一轮迁移，确保 stage/options 列和索引都存在
            if not questions_exists:
                self._migrate_add_stage_column(conn)
                self._migrate_add_options_column(conn)

            logger.info(f"数据库初始化完成: {self.db_path}")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            raise

    def _create_tables_inline(self, conn: sqlite3.Connection):
        """内联建表（当 schema.sql 不可用时使用）。"""
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS questions (
                id              TEXT PRIMARY KEY,
                subject         TEXT NOT NULL,
                answer          TEXT,
                knowledge_point TEXT,
                sub_knowledge   TEXT,
                difficulty      TEXT,
                error_prone     TEXT,
                grade_level     TEXT,
                stage           TEXT,
                question_type   TEXT,
                options         TEXT,
                has_image       INTEGER DEFAULT 0,
                source_file     TEXT,
                source_page     INTEGER,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used_count      INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS images (
                id          TEXT PRIMARY KEY,
                question_id TEXT NOT NULL,
                filename    TEXT,
                filepath    TEXT,
                page_num    INTEGER,
                FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS knowledge_tags (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT UNIQUE NOT NULL,
                parent_id   INTEGER,
                grade_level TEXT,
                FOREIGN KEY (parent_id) REFERENCES knowledge_tags(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS lecture_sessions (
                id           TEXT PRIMARY KEY,
                grade_level  TEXT,
                day_number   INTEGER,
                topic        TEXT,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                config_json  TEXT
            );

            CREATE TABLE IF NOT EXISTS session_questions (
                session_id  TEXT NOT NULL,
                question_id TEXT NOT NULL,
                section     TEXT,
                layer       TEXT,
                sort_order  INTEGER,
                PRIMARY KEY (session_id, question_id),
                FOREIGN KEY (session_id) REFERENCES lecture_sessions(id) ON DELETE CASCADE,
                FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_questions_knowledge_point ON questions(knowledge_point);
            CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions(difficulty);
            CREATE INDEX IF NOT EXISTS idx_questions_grade_level ON questions(grade_level);
            CREATE INDEX IF NOT EXISTS idx_questions_stage ON questions(stage);
            CREATE INDEX IF NOT EXISTS idx_questions_used_count ON questions(used_count);
            CREATE INDEX IF NOT EXISTS idx_images_question_id ON images(question_id);
            CREATE INDEX IF NOT EXISTS idx_session_questions_session ON session_questions(session_id);
            """
        )

    def _migrate_add_stage_column(self, conn: sqlite3.Connection):
        """迁移：为 questions 表添加 stage 列（如果不存在）。"""
        cursor = conn.execute("PRAGMA table_info(questions)")
        columns = [row[1] for row in cursor.fetchall()]
        if "stage" not in columns:
            conn.execute("ALTER TABLE questions ADD COLUMN stage TEXT")
            # 为已有数据填充 stage（根据 grade_level 推导）
            for grade, stage in STAGE_MAP.items():
                conn.execute(
                    "UPDATE questions SET stage = ? WHERE grade_level = ?",
                    (stage, grade),
                )
            conn.commit()
            logger.info("迁移完成：questions 表已添加 stage 列并填充数据")

    def _migrate_add_options_column(self, conn: sqlite3.Connection):
        """迁移：为 questions 表添加 options 列（如果不存在）。"""
        cursor = conn.execute("PRAGMA table_info(questions)")
        columns = [row[1] for row in cursor.fetchall()]
        if "options" not in columns:
            conn.execute("ALTER TABLE questions ADD COLUMN options TEXT")
            conn.commit()
            logger.info("迁移完成：questions 表已添加 options 列")

    # ---------- 辅助方法 ----------

    def _validate_question_data(self, data: Dict[str, Any]):
        """校验题目数据基本合法性，并兜底归一化 AI 返回的非法值。"""
        if not isinstance(data, dict):
            raise TypeError("data 必须是字典类型")
        if not data.get("subject"):
            raise ValueError("subject 不能为空")

        difficulty = data.get("difficulty", "")
        if difficulty and difficulty not in VALID_DIFFICULTIES:
            logger.info(f"难度值归一化: {difficulty!r} -> 基础")
            data["difficulty"] = "基础"

        grade_level = data.get("grade_level")
        if grade_level and grade_level not in VALID_GRADE_LEVELS:
            raise ValueError(f"无效的学段值: {grade_level}，允许的值: {VALID_GRADE_LEVELS}")

    def _text_similarity(self, text1: str, text2: str) -> float:
        """计算两段文本的相似度（0.0 ~ 1.0）。"""
        if not text1 or not text2:
            return 0.0
        return SequenceMatcher(None, text1, text2).ratio()

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """将 sqlite3.Row 转换为普通字典。"""
        return dict(row) if row else None

    # ---------- 插入 ----------

    def insert_question(self, data: Dict[str, Any]) -> Optional[str]:
        """插入一道题目，自动去重检查。

        去重逻辑：相同知识点 + 题干相似度 > 90% 视为重复。

        Args:
            data: 题目数据字典

        Returns:
            插入成功返回题目 UUID，重复时返回 None
        """
        self._validate_question_data(data)

        question_id = data.get("id") or str(uuid.uuid4())
        conn = self._ensure_connected()

        # 去重检查：查找同知识点下相似题目
        knowledge = data.get("knowledge_point", "")
        if knowledge:
            existing = self.find_similar(data["subject"], knowledge)
            if existing:
                logger.info(f"发现重复题目，跳过插入: {question_id}")
                return None

        try:
            # 自动推导 stage
            grade_level = data.get("grade_level", "")
            stage = STAGE_MAP.get(grade_level, None)

            conn.execute(
                """
                INSERT INTO questions (id, subject, answer, knowledge_point, sub_knowledge,
                    difficulty, error_prone, grade_level, stage, question_type, options,
                    has_image, source_file, source_page, used_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    question_id,
                    data.get("subject", ""),
                    data.get("answer", ""),
                    data.get("knowledge_point", ""),
                    data.get("sub_knowledge", ""),
                    data.get("difficulty", ""),
                    data.get("error_prone", ""),
                    grade_level,
                    stage,
                    data.get("question_type", ""),
                    json.dumps(data.get("options", []), ensure_ascii=False) if isinstance(data.get("options", []), list) else data.get("options", ""),
                    int(bool(data.get("has_image", False))),
                    data.get("source_file", ""),
                    data.get("source_page", 0),
                ),
            )

            # 插入关联图片
            images = data.get("images", [])
            for img in images:
                img_id = img.get("id") or str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO images (id, question_id, filename, filepath, page_num) VALUES (?, ?, ?, ?, ?)",
                    (
                        img_id,
                        question_id,
                        img.get("filename", ""),
                        img.get("filepath", ""),
                        img.get("page_num", 0),
                    ),
                )

            logger.info(f"题目已插入: {question_id}")
            return question_id

        except sqlite3.IntegrityError as e:
            logger.warning(f"题目插入失败（完整性约束）: {e}")
            return None
        except Exception as e:
            logger.error(f"题目插入失败: {e}")
            raise

    # ---------- 批量导入 ----------

    def insert_batch(self, questions: List[Dict[str, Any]]) -> List[Optional[str]]:
        """批量插入题目（逐题提交，单题失败不影响其他题目）。

        Args:
            questions: 题目数据列表

        Returns:
            成功插入的题目 ID 列表（None 表示跳过/失败）
        """
        if not isinstance(questions, list):
            raise TypeError("questions 必须是列表类型")

        conn = self._ensure_connected()
        results = []
        inserted = 0
        failed = 0

        for data in questions:
            conn.execute("BEGIN")
            try:
                result = self.insert_question(data)
                conn.execute("COMMIT")
                results.append(result)
                if result is not None:
                    inserted += 1
            except Exception:
                conn.execute("ROLLBACK")
                results.append(None)
                failed += 1

        logger.info(f"批量插入完成: {inserted} 道题, 跳过/失败 {failed} 道")
        return results

    # ---------- 查询 ----------

    def find_similar(self, question_text: str, knowledge_point: str = "") -> Optional[Dict[str, Any]]:
        """查找相似题目（基于知识点 + 题干相似度）。

        Args:
            question_text: 题干文本
            knowledge_point: 知识点（可选过滤）

        Returns:
            找到的相似题目字典，未找到返回 None
        """
        if not question_text:
            raise ValueError("question_text 不能为空")

        conn = self._ensure_connected()
        try:
            if knowledge_point:
                rows = conn.execute(
                    "SELECT * FROM questions WHERE knowledge_point = ?",
                    (knowledge_point,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM questions").fetchall()

            for row in rows:
                existing = self._row_to_dict(row)
                similarity = self._text_similarity(question_text, existing["subject"])
                if similarity >= SIMILARITY_THRESHOLD:
                    logger.info(f"找到相似题目: {existing['id']} (相似度: {similarity:.2%})")
                    return existing

            return None

        except Exception as e:
            logger.error(f"查找相似题目失败: {e}")
            raise

    def get_questions_by_filter(
        self,
        knowledge: Optional[str] = None,
        difficulty: Optional[str] = None,
        grade: Optional[str] = None,
        exclude_ids: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """按条件检索题目。

        Args:
            knowledge: 知识点过滤
            difficulty: 难度过滤
            grade: 学段过滤
            exclude_ids: 排除的题目 ID 列表
            limit: 最大返回数量

        Returns:
            题目列表
        """
        if limit < 1:
            raise ValueError("limit 必须 >= 1")

        conn = self._ensure_connected()
        conditions = []
        params = []

        if knowledge:
            conditions.append("knowledge_point = ?")
            params.append(knowledge)
        if difficulty:
            conditions.append("difficulty = ?")
            params.append(difficulty)
        if grade:
            equiv = EQUIVALENT_GRADES.get(grade)
            if equiv == "小学" or equiv == "初中" or equiv == "高中":
                # 通配学段：按 stage 列过滤
                conditions.append("stage = ?")
                params.append(equiv)
            elif isinstance(equiv, list):
                # 衔接学段：IN 查询等价学段列表
                placeholders = ",".join("?" for _ in equiv)
                conditions.append(f"grade_level IN ({placeholders})")
                params.extend(equiv)
            else:
                # 普通学期学段：精确匹配
                conditions.append("grade_level = ?")
                params.append(grade)
        if exclude_ids:
            placeholders = ",".join("?" for _ in exclude_ids)
            conditions.append(f"id NOT IN ({placeholders})")
            params.extend(exclude_ids)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM questions WHERE {where_clause} ORDER BY used_count ASC LIMIT ?"
        params.append(limit)

        try:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"条件查询失败: {e}")
            raise

    def get_questions_by_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        """批量获取题目。

        Args:
            ids: 题目 ID 列表

        Returns:
            题目列表
        """
        if not ids:
            return []
        if not isinstance(ids, list):
            raise TypeError("ids 必须是列表类型")

        conn = self._ensure_connected()
        placeholders = ",".join("?" for _ in ids)
        query = f"SELECT * FROM questions WHERE id IN ({placeholders})"

        try:
            rows = conn.execute(query, ids).fetchall()
            return [self._row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"批量获取题目失败: {e}")
            raise

    def get_images_by_question_ids(self, question_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """批量查询多道题目的关联图片。

        Args:
            question_ids: 题目 ID 列表

        Returns:
            {question_id: [{filename, filepath, ...}, ...], ...}
        """
        if not question_ids:
            return {}
        conn = self._ensure_connected()
        placeholders = ",".join("?" for _ in question_ids)
        query = f"SELECT * FROM images WHERE question_id IN ({placeholders})"

        try:
            rows = conn.execute(query, question_ids).fetchall()
            result: Dict[str, List[Dict[str, Any]]] = {}
            for row in rows:
                d = self._row_to_dict(row)
                qid = d["question_id"]
                if qid not in result:
                    result[qid] = []
                result[qid].append(d)
            return result
        except Exception as e:
            logger.error(f"批量查询图片失败: {e}")
            raise

    def get_unused_questions(
        self,
        knowledge: Optional[str] = None,
        difficulty: Optional[str] = None,
        grade: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """优先返回未使用过（used_count = 0）的题目。

        Args:
            knowledge: 知识点过滤
            difficulty: 难度过滤
            grade: 学段过滤
            limit: 最大返回数量

        Returns:
            题目列表（按 used_count 升序排列）
        """
        if limit < 1:
            raise ValueError("limit 必须 >= 1")

        conn = self._ensure_connected()
        conditions = []
        params = []

        if knowledge:
            conditions.append("knowledge_point = ?")
            params.append(knowledge)
        if difficulty:
            conditions.append("difficulty = ?")
            params.append(difficulty)
        if grade:
            equiv = EQUIVALENT_GRADES.get(grade)
            if equiv == "小学" or equiv == "初中" or equiv == "高中":
                # 通配学段：按 stage 列过滤
                conditions.append("stage = ?")
                params.append(equiv)
            elif isinstance(equiv, list):
                # 衔接学段：IN 查询等价学段列表
                placeholders = ",".join("?" for _ in equiv)
                conditions.append(f"grade_level IN ({placeholders})")
                params.extend(equiv)
            else:
                # 普通学期学段：精确匹配
                conditions.append("grade_level = ?")
                params.append(grade)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = (
            f"SELECT * FROM questions WHERE {where_clause} "
            "ORDER BY used_count ASC, created_at DESC LIMIT ?"
        )
        params.append(limit)

        try:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"查询未使用题目失败: {e}")
            raise

    # ---------- 更新 ----------

    def record_usage(self, question_id: str) -> bool:
        """增加题目使用计数。

        Args:
            question_id: 题目 ID

        Returns:
            是否更新成功
        """
        if not question_id:
            raise ValueError("question_id 不能为空")

        conn = self._ensure_connected()
        try:
            cursor = conn.execute(
                "UPDATE questions SET used_count = used_count + 1 WHERE id = ?",
                (question_id,),
            )
            conn.commit()
            success = cursor.rowcount > 0
            if success:
                logger.info(f"题目使用计数已更新: {question_id}")
            else:
                logger.warning(f"题目不存在: {question_id}")
            return success
        except Exception as e:
            logger.error(f"更新使用计数失败: {e}")
            raise

    # ---------- 讲义操作 ----------

    def create_lecture_session(
        self,
        grade_level: str,
        day_number: int,
        topic: str,
        config_json: Optional[Dict] = None,
    ) -> str:
        """创建讲义生成记录。

        Args:
            grade_level: 学段
            day_number: 第几天
            topic: 主题
            config_json: 生成配置

        Returns:
            讲义 UUID
        """
        if not grade_level:
            raise ValueError("grade_level 不能为空")
        if not topic:
            raise ValueError("topic 不能为空")
        if not isinstance(day_number, int) or day_number < 1:
            raise ValueError("day_number 必须是正整数")

        session_id = str(uuid.uuid4())
        conn = self._ensure_connected()

        try:
            conn.execute(
                """
                INSERT INTO lecture_sessions (id, grade_level, day_number, topic, config_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    grade_level,
                    day_number,
                    topic,
                    json.dumps(config_json, ensure_ascii=False) if config_json else None,
                ),
            )
            conn.commit()
            logger.info(f"讲义记录已创建: {session_id}")
            return session_id
        except Exception as e:
            logger.error(f"创建讲义记录失败: {e}")
            raise

    def add_question_to_session(
        self,
        session_id: str,
        question_id: str,
        section: str = "",
        layer: str = "",
        sort_order: int = 0,
    ) -> bool:
        """将题目添加到讲义。

        Args:
            session_id: 讲义 ID
            question_id: 题目 ID
            section: 所属环节
            layer: 分层（A/B/C）
            sort_order: 排序

        Returns:
            是否成功
        """
        if not session_id or not question_id:
            raise ValueError("session_id 和 question_id 不能为空")

        conn = self._ensure_connected()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO session_questions (session_id, question_id, section, layer, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, question_id, section, layer, sort_order),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"添加题目到讲义失败: {e}")
            raise

    def get_session_questions(self, session_id: str) -> List[Dict[str, Any]]:
        """获取讲义中的所有题目。

        Args:
            session_id: 讲义 ID

        Returns:
            题目列表（含讲义关联信息）
        """
        if not session_id:
            raise ValueError("session_id 不能为空")

        conn = self._ensure_connected()
        try:
            rows = conn.execute(
                """
                SELECT q.*, sq.section, sq.layer, sq.sort_order
                FROM session_questions sq
                JOIN questions q ON sq.question_id = q.id
                WHERE sq.session_id = ?
                ORDER BY sq.sort_order ASC
                """,
                (session_id,),
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error(f"获取讲义题目失败: {e}")
            raise

    # ---------- 导入 / 导出 ----------

    def export_to_json(self, filepath: str):
        """导出全库为 JSON 文件。

        Args:
            filepath: 输出 JSON 文件路径
        """
        if not filepath:
            raise ValueError("filepath 不能为空")

        conn = self._ensure_connected()
        try:
            # 导出 questions
            questions = [
                self._row_to_dict(row)
                for row in conn.execute("SELECT * FROM questions").fetchall()
            ]

            # 导出 images
            images = [
                self._row_to_dict(row)
                for row in conn.execute("SELECT * FROM images").fetchall()
            ]

            # 导出 knowledge_tags
            tags = [
                self._row_to_dict(row)
                for row in conn.execute("SELECT * FROM knowledge_tags").fetchall()
            ]

            # 导出 lecture_sessions
            sessions = [
                self._row_to_dict(row)
                for row in conn.execute("SELECT * FROM lecture_sessions").fetchall()
            ]

            # 导出 session_questions
            session_qs = [
                self._row_to_dict(row)
                for row in conn.execute("SELECT * FROM session_questions").fetchall()
            ]

            export_data = {
                "questions": questions,
                "images": images,
                "knowledge_tags": tags,
                "lecture_sessions": sessions,
                "session_questions": session_qs,
                "exported_at": sqlite3.datetime.datetime.now().isoformat() if hasattr(sqlite3, "datetime") else "",
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)

            logger.info(f"数据库已导出到: {filepath} (共 {len(questions)} 道题)")

        except Exception as e:
            logger.error(f"导出失败: {e}")
            raise

    def import_from_json(self, filepath: str):
        """从 JSON 文件导入数据库。

        Args:
            filepath: 输入 JSON 文件路径
        """
        if not filepath:
            raise ValueError("filepath 不能为空")
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在: {filepath}")

        conn = self._ensure_connected()

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                raise ValueError("JSON 文件格式错误：根元素必须是对象")

            conn.execute("BEGIN")

            # 导入 knowledge_tags
            for tag in data.get("knowledge_tags", []):
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_tags (id, name, parent_id, grade_level) VALUES (?, ?, ?, ?)",
                    (tag.get("id"), tag.get("name"), tag.get("parent_id"), tag.get("grade_level")),
                )

            # 导入 questions
            for q in data.get("questions", []):
                grade_level = q.get("grade_level", "")
                stage = STAGE_MAP.get(grade_level, None)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO questions
                    (id, subject, answer, knowledge_point, sub_knowledge, difficulty,
                     error_prone, grade_level, stage, question_type, options, has_image,
                     source_file, source_page, created_at, used_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        q.get("id"),
                        q.get("subject"),
                        q.get("answer"),
                        q.get("knowledge_point"),
                        q.get("sub_knowledge"),
                        q.get("difficulty"),
                        q.get("error_prone"),
                        grade_level,
                        stage,
                        q.get("question_type"),
                        json.dumps(q.get("options", []), ensure_ascii=False) if isinstance(q.get("options", []), list) else q.get("options", ""),
                        q.get("has_image", 0),
                        q.get("source_file"),
                        q.get("source_page"),
                        q.get("created_at"),
                        q.get("used_count", 0),
                    ),
                )

            # 导入 images
            for img in data.get("images", []):
                conn.execute(
                    "INSERT OR REPLACE INTO images (id, question_id, filename, filepath, page_num) VALUES (?, ?, ?, ?, ?)",
                    (img.get("id"), img.get("question_id"), img.get("filename"), img.get("filepath"), img.get("page_num")),
                )

            # 导入 lecture_sessions
            for session in data.get("lecture_sessions", []):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO lecture_sessions
                    (id, grade_level, day_number, topic, generated_at, config_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.get("id"),
                        session.get("grade_level"),
                        session.get("day_number"),
                        session.get("topic"),
                        session.get("generated_at"),
                        session.get("config_json"),
                    ),
                )

            # 导入 session_questions
            for sq in data.get("session_questions", []):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO session_questions
                    (session_id, question_id, section, layer, sort_order)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (sq.get("session_id"), sq.get("question_id"), sq.get("section"), sq.get("layer"), sq.get("sort_order")),
                )

            conn.execute("COMMIT")
            logger.info(
                f"从 JSON 导入完成: {filepath} "
                f"(题目: {len(data.get('questions', []))}, "
                f"图片: {len(data.get('images', []))})"
            )

        except Exception as e:
            conn.execute("ROLLBACK")
            logger.error(f"从 JSON 导入失败: {e}")
            raise

    # ---------- 统计 ----------

    def get_stats(self) -> Dict[str, Any]:
        """获取数据库统计信息。"""
        conn = self._ensure_connected()
        try:
            total = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            unused = conn.execute("SELECT COUNT(*) FROM questions WHERE used_count = 0").fetchone()[0]
            by_difficulty = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT difficulty, COUNT(*) FROM questions GROUP BY difficulty"
                ).fetchall()
                if row[0]
            }
            by_grade = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT grade_level, COUNT(*) FROM questions GROUP BY grade_level"
                ).fetchall()
                if row[0]
            }
            by_stage = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT stage, COUNT(*) FROM questions GROUP BY stage"
                ).fetchall()
                if row[0]
            }

            return {
                "total_questions": total,
                "unused_questions": unused,
                "by_difficulty": by_difficulty,
                "by_grade": by_grade,
                "by_stage": by_stage,
            }
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            raise


# ---------- 便捷函数 ----------

def create_database(db_path: str = DEFAULT_DB_PATH) -> QuestionBankDB:
    """创建并初始化数据库的便捷函数。

    Args:
        db_path: 数据库文件路径

    Returns:
        已初始化的 QuestionBankDB 实例
    """
    db = QuestionBankDB(db_path)
    db.init_db()
    return db
