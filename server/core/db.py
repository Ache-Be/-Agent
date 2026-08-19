# _*_ coding : UTF-8 _*_
"""
PostgreSQL + pgvector 连接层
- SQLAlchemy 引擎 + 线程安全连接池
- PgvectorStore: 向量读写（幂等 upsert）+ 结构化聚合查询 + 混合检索
  （结构化 WHERE 精确匹配 + 向量 <=> 余弦距离排序）
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union

from loguru import logger
from sqlalchemy import (
    Column, BigInteger, Integer, SmallInteger, String, Text, Numeric, Boolean,
    DateTime, func, text, Index, and_, or_, ARRAY, select, ForeignKey, case,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import (
    DeclarativeBase, Session, sessionmaker, relationship,
    Mapped, mapped_column,
)
try:
    from pgvector.sqlalchemy import Vector
    HAS_PGVECTOR = True
except Exception:  # pragma: no cover - 安装前兜底避免 import 失败
    Vector = None
    HAS_PGVECTOR = False

try:
    from .config import config
except ImportError:
    from core.config import config  # pragma: no cover - standalone mode fallback

# ========================= ORM Base =========================
class Base(DeclarativeBase):
    pass


# ========================= ORM Models (与 001_init.sql 1:1 对齐 =========================
class UploadedFile(Base):
    __tablename__ = "uploaded_files"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_hash: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    safe_name: Mapped[str] = mapped_column(String(512), nullable=False)
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    experiment_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_student: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_noise: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_teacher: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ingested")
    error_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    file_metadata: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    student_rows: Mapped[List["StudentRow"]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
        primaryjoin="UploadedFile.id == foreign(StudentRow.file_id)",
    )


class StudentRow(Base):
    __tablename__ = "student_rows"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("uploaded_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    row_type: Mapped[str] = mapped_column(String(32), nullable=False, default="student")
    student_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    class_name: Mapped[str] = mapped_column(String(256), nullable=False, default="", index=True)
    experiment_name: Mapped[str] = mapped_column(String(512), nullable=False, default="", index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    final_score: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    weak_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    task_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    row_text: Mapped[str] = mapped_column(Text, nullable=False)
    extra_cols: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    embedding: Mapped[Any] = mapped_column(Vector(config.embedding_vector_dim), nullable=False) if HAS_PGVECTOR else mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_student_rows_cls_exp", "class_name", "experiment_name"),
        Index("idx_student_rows_file_line_unique", "file_id", "line_no", unique=True),
    )

    file: Mapped[UploadedFile] = relationship(
        back_populates="student_rows",
        primaryjoin="StudentRow.file_id == UploadedFile.id",
    )


# ========================= 引擎 / 会话 =========================
_LOCK = threading.RLock()
_engine = None
_SessionLocal: Optional[sessionmaker] = None


def _init_engine(force: bool = False):
    """惰性创建 SQLAlchemy engine + sessionmaker（线程安全）"""
    global _engine, _SessionLocal
    with _LOCK:
        if _engine is not None and not force:
            return
        from sqlalchemy import create_engine
        url = config.build_db_url()
        _engine = create_engine(
            url,
            pool_size=config.db_pool_size,
            max_overflow=config.db_max_overflow,
            pool_timeout=config.db_pool_timeout,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=config.db_echo,
            future=True,
        )
        # pgvector 0.3.x: Vector type auto-registers on import; no explicit register_vector() needed in 0.3.5+
        # (legacy register_vector() API removed in newer pgvector releases)
        _SessionLocal = sessionmaker(
            bind=_engine, expire_on_commit=False, autoflush=False, future=True
        )


def get_engine():
    _init_engine()
    return _engine


@contextmanager
def db_session() -> Iterator[Session]:
    """
    线程安全的会话上下文管理器。
    用法:
        with db_session() as s:
            s.add(...)
            s.commit()
    """
    _init_engine()
    session: Session = _SessionLocal()
    try:
        yield session
    except SQLAlchemyError as e:
        session.rollback()
        logger.exception("DB 操作异常，已自动 rollback: %s", e)
        raise
    finally:
        session.close()


def run_migration_if_needed(migration_path: Optional[Path] = None) -> Tuple[bool, str]:
    """
    幂等执行 001_init.sql：检查表是否存在就跳过，不存在就 source SQL。
    返回 (是否有执行过 SQL, 状态描述)
    """
    migration_path = migration_path or (
        Path(__file__).parent.parent / "db" / "migrations" / "001_init.sql"
    )
    if not migration_path.exists():
        return False, f"Migration 文件不存在: {migration_path}"
    # 先判断是否已执行过：简单看 schema_migrations 表是否有 001_init
    try:
        with db_session() as s:
            from sqlalchemy import Table, MetaData
            meta = MetaData()
            try:
                meta.reflect(bind=s.get_bind(), only=["schema_migrations"])
            except Exception:
                pass
            if "schema_migrations" in meta.tables:
                row = s.execute(
                    text("SELECT 1 FROM schema_migrations WHERE version='001_init' LIMIT 1")
                ).fetchone()
                if row:
                    return False, "migration 001_init 已执行，跳过"
            sql_text = migration_path.read_text(encoding="utf-8")
            # sql 按 ; 拆分多行注释单独执行
            statements = [s.strip() for s in sql_text.split(";") if s.strip()]
            for st in statements:
                # 跳过 comment 行
                lines = [l for l in st.splitlines() if not l.strip().startswith("--")]
                exec_sql = "\n".join(lines).strip()
                if not exec_sql:
                    continue
                s.execute(text(exec_sql))
            s.commit()
            return True, "001_init.sql 执行成功"
    except Exception as e:
        logger.exception("run_migration_if_needed 失败: %s", e)
        return False, f"Migration 失败: {e}"


# ========================= PgvectorStore: 向量 & 结构化查询对外接口 =========================
class PgvectorStore:
    """
    pgvector 存储封装（只暴露与业务无关的增删改查 / 混合检索）。
    业务逻辑层放在 ingest_service / rag_service 里，保持分离。
    """

    # -------- UploadedFiles --------
    @staticmethod
    def upsert_uploaded_file(
        file_hash: str,
        safe_name: str,
        original_name: str,
        relative_path: str = "",
        file_size: int = 0,
        source_type: str = "",
        experiment_name: str = "",
        status: str = "ingested",
        metadata: Optional[Dict] = None,
    ) -> UploadedFile:
        """文件级 upsert：file_hash 唯一。重复上传不会创建新记录，只更新计数"""
        with db_session() as s:
            existing = s.query(UploadedFile).filter_by(file_hash=file_hash).one_or_none()
            if existing is not None:
                existing.uploaded_at = func.now()
                if metadata:
                    existing.file_metadata = {**(existing.file_metadata or {}), **metadata}
                s.commit()
                s.refresh(existing)
                return existing
            rec = UploadedFile(
                file_hash=file_hash,
                safe_name=safe_name,
                original_name=original_name,
                relative_path=relative_path,
                file_size=file_size,
                source_type=source_type,
                experiment_name=experiment_name,
                status=status,
                file_metadata=metadata or {},
            )
            s.add(rec)
            s.commit()
            s.refresh(rec)
            return rec

    @staticmethod
    def mark_uploaded_rows(
        file_id: int,
        rows_total: int,
        rows_student: int,
        rows_noise: int,
        rows_teacher: int,
        error_msg: Optional[str] = None,
    ):
        with db_session() as s:
            rec = s.get(UploadedFile, file_id)
            if rec is None:
                return
            rec.rows_total = rows_total
            rec.rows_student = rows_student
            rec.rows_noise = rows_noise
            rec.rows_teacher = rows_teacher
            rec.status = "ingested" if error_msg is None else "error"
            if error_msg:
                rec.error_msg = error_msg
            s.commit()

    @staticmethod
    def delete_uploaded_file(file_hash: Optional[str] = None, file_id: Optional[int] = None):
        """级联删除：student_rows 会被 ON DELETE CASCADE 自动清掉"""
        with db_session() as s:
            q = s.query(UploadedFile)
            if file_hash:
                q = q.filter_by(file_hash=file_hash)
            elif file_id:
                q = q.filter_by(id=file_id)
            else:
                raise ValueError("file_hash / file_id 必须传一个")
            q.delete(synchronize_session=False)
            s.commit()

    @staticmethod
    def list_uploaded_files(limit: int = 500) -> List[Dict]:
        with db_session() as s:
            rows = (
                s.query(UploadedFile)
                .order_by(UploadedFile.uploaded_at.desc())
                .limit(limit)
                .all()
            )
            return [PgvectorStore._to_dict_file(r) for r in rows]

    @staticmethod
    def _to_dict_file(r: UploadedFile) -> Dict:
        return {
            "id": r.id, "file_hash": r.file_hash, "safe_name": r.safe_name,
            "original_name": r.original_name, "relative_path": r.relative_path,
            "file_size": r.file_size, "source_type": r.source_type,
            "experiment_name": r.experiment_name, "rows_total": r.rows_total,
            "rows_student": r.rows_student, "rows_noise": r.rows_noise,
            "rows_teacher": r.rows_teacher, "status": r.status,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
        }

    # -------- StudentRows (批量幂等 upsert) --------
    @staticmethod
    def bulk_upsert_student_rows(
        file_id: int,
        rows: Iterable[Dict[str, Any]],
        vector_dim: int = 512,
    ) -> Tuple[int, int]:
        """
        批量 upsert 学生行。
        rows: 每个 dict 必须包含 line_no / row_type / student_id / name /
              class_name / experiment_name / source_type / final_score /
              weak_count / task_count / row_text / extra_cols / embedding
        唯一约束: (file_id, line_no) 冲突时 UPDATE。
        返回: (新增条数, 更新条数)
        """
        if not HAS_PGVECTOR:
            raise RuntimeError("pgvector 未安装，无法写入向量列。请先安装 pgvector 并重启服务。")
        rows_list = list(rows)
        if not rows_list:
            return 0, 0

        # 为了性能，先按 (file_id, line_no) 查哪些已存在 → 分开批量 insert / update
        with db_session() as s:
            line_no_set = {r["line_no"] for r in rows_list}
            existing = {
                x[0]: x[1] for x in s.execute(
                    select(StudentRow.line_no, StudentRow.id).where(
                        StudentRow.file_id == file_id,
                        StudentRow.line_no.in_(list(line_no_set))
                    )
                ).all()
            }
            inserted = 0
            updated = 0
            insert_objs: List[StudentRow] = []
            update_payloads: List[Tuple[int, Dict]] = []
            for r in rows_list:
                ln = r["line_no"]
                payload = dict(
                    row_type=r.get("row_type", "student"),
                    student_id=r.get("student_id", "") or "",
                    name=r.get("name", "") or "",
                    class_name=r.get("class_name", "") or "",
                    experiment_name=r.get("experiment_name", "") or "",
                    source_type=r.get("source_type", "") or "",
                    final_score=r.get("final_score"),
                    weak_count=int(r.get("weak_count", 0) or 0),
                    task_count=int(r.get("task_count", 0) or 0),
                    row_text=r.get("row_text", ""),
                    extra_cols=r.get("extra_cols", {}) or {},
                    embedding=r["embedding"],
                )
                if ln in existing:
                    update_payloads.append((existing[ln], payload))
                else:
                    insert_objs.append(StudentRow(
                        file_id=file_id, line_no=ln, **payload
                    ))
            if insert_objs:
                s.add_all(insert_objs)
                inserted = len(insert_objs)
            for sid, p in update_payloads:
                s.query(StudentRow).filter_by(id=sid).update(p, synchronize_session=False)
                updated += 1
            s.commit()
            return inserted, updated

    # -------- 混合检索：结构化 WHERE + 向量相似度 --------
    @staticmethod
    def hybrid_search(
        query_embedding: List[float],
        *,
        student_id: Optional[str] = None,
        name: Optional[str] = None,
        class_name: Optional[str] = None,
        experiment_name: Optional[str] = None,
        source_type: Optional[str] = None,
        min_score: Optional[float] = None,
        max_score: Optional[float] = None,
        top_k: int = 20,
        vector_only: bool = False,
    ) -> List[Dict]:
        """
        混合检索：
          1) 先按结构化条件 WHERE 过滤
          2) 再对子集做向量余弦距离排序，取 top_k
        返回 dict 含: {...学生行字段..., similarity (1 - 余弦距离)}
        """
        with db_session() as s:
            conditions = [StudentRow.row_type == "student"]
            if not vector_only:
                if student_id:
                    conditions.append(StudentRow.student_id == student_id)
                if name:
                    conditions.append(
                        or_(StudentRow.name == name, StudentRow.name.like(f"%{name}%"))
                    )
                if class_name:
                    conditions.append(
                        or_(
                            StudentRow.class_name == class_name,
                            StudentRow.class_name.like(f"%{class_name}%"),
                        )
                    )
                if experiment_name:
                    conditions.append(
                        or_(
                            StudentRow.experiment_name == experiment_name,
                            StudentRow.experiment_name.like(f"%{experiment_name}%"),
                        )
                    )
                if source_type:
                    conditions.append(StudentRow.source_type == source_type)
                if min_score is not None:
                    conditions.append(StudentRow.final_score >= min_score)
                if max_score is not None:
                    conditions.append(StudentRow.final_score <= max_score)

            # 1 - cosine distance 就是 cosine similarity
            sim_expr = (1 - StudentRow.embedding.cosine_distance(query_embedding)).label("similarity")
            stmt = (
                select(
                    StudentRow.id, StudentRow.file_id, StudentRow.line_no,
                    StudentRow.student_id, StudentRow.name, StudentRow.class_name,
                    StudentRow.experiment_name, StudentRow.source_type,
                    StudentRow.final_score, StudentRow.weak_count, StudentRow.task_count,
                    StudentRow.row_text, StudentRow.extra_cols, StudentRow.created_at,
                    sim_expr,
                )
                .where(and_(*conditions))
                .order_by(sim_expr.desc())
                .limit(top_k)
            )
            rows = s.execute(stmt).all()
            out: List[Dict] = []
            for r in rows:
                out.append({
                    "id": r.id, "file_id": r.file_id, "line_no": r.line_no,
                    "student_id": r.student_id, "name": r.name,
                    "class_name": r.class_name, "experiment_name": r.experiment_name,
                    "source_type": r.source_type,
                    "final_score": float(r.final_score) if r.final_score is not None else None,
                    "weak_count": r.weak_count, "task_count": r.task_count,
                    "row_text": r.row_text,
                    "extra_cols": r.extra_cols,
                    "similarity": float(r.similarity),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })
            return out

    # -------- 结构化聚合查询（给仪表盘/统计接口）--------
    @staticmethod
    def wipe_all_uploads() -> None:
        """
        覆盖模式：清空 uploaded_files + 关联的 student_rows。
        注意：外键 ON DELETE CASCADE 会级联删 student_rows，这里再显式执行一次更保险。
        """
        with db_session() as s:
            s.execute(text("TRUNCATE student_rows CASCADE"))
            s.execute(text("TRUNCATE uploaded_files CASCADE"))
            s.commit()

    @staticmethod
    def overview_stats() -> Dict[str, Any]:
        """首页大盘统计"""
        with db_session() as s:
            q = s.query(UploadedFile)
            file_count = q.count()

            stu = s.query(func.count(func.distinct(StudentRow.student_id))).filter(
                StudentRow.row_type == "student", StudentRow.student_id != ""
            ).scalar() or 0

            classes = s.query(func.count(func.distinct(StudentRow.class_name))).filter(
                StudentRow.row_type == "student", StudentRow.class_name != ""
            ).scalar() or 0

            exps = s.query(func.count(func.distinct(StudentRow.experiment_name))).filter(
                StudentRow.row_type == "student", StudentRow.experiment_name != ""
            ).scalar() or 0

            avg, weak_rate = s.execute(
                select(
                    func.round(func.avg(StudentRow.final_score).cast(Numeric(10, 2)), 2),
                    func.round(
                        100.0 * func.sum(case((StudentRow.final_score < 60, 1), else_=0))
                        / func.nullif(func.count(), 0),
                        2
                    ),
                ).where(
                    StudentRow.row_type == "student", StudentRow.final_score.isnot(None)
                )
            ).one()
            return {
                "file_count": file_count,
                "student_count": stu,
                "class_count": classes,
                "experiment_count": exps,
                "avg_score": float(avg) if avg is not None else 0.0,
                "weak_rate_percent": float(weak_rate) if weak_rate is not None else 0.0,
            }

    @staticmethod
    def student_summary(
        *,
        class_name: Optional[str] = None,
        student_id: Optional[str] = None,
        name: Optional[str] = None,
        min_weak_rate: Optional[float] = None,
        max_avg_score: Optional[float] = None,
        limit: int = 500,
        sort_by: str = "avg_score",
        sort_desc: bool = True,
    ) -> List[Dict]:
        """班级/学生聚合视图查询"""
        with db_session() as s:
            sql = "SELECT * FROM v_student_summary WHERE 1=1"
            params: Dict[str, Any] = {}
            if class_name:
                sql += " AND class_name = :c"
                params["c"] = class_name
            if student_id:
                sql += " AND student_id = :sid"
                params["sid"] = student_id
            if name:
                sql += " AND name LIKE :n"
                params["n"] = f"%{name}%"
            if min_weak_rate is not None:
                sql += " AND weak_rate_percent >= :mw"
                params["mw"] = float(min_weak_rate)
            if max_avg_score is not None:
                sql += " AND avg_score <= :ma"
                params["ma"] = float(max_avg_score)
            order_col = "avg_score" if sort_by not in (
                "avg_score", "weak_rate_percent", "experiment_count", "weak_count"
            ) else sort_by
            sql += f" ORDER BY {order_col} {'DESC' if sort_desc else 'ASC'}"
            sql += " LIMIT :lim"
            params["lim"] = int(limit)
            rows = s.execute(text(sql), params).mappings().all()
            return [dict(r) for r in rows]

    @staticmethod
    def class_summary(
        *,
        class_name: Optional[str] = None,
        experiment_name: Optional[str] = None,
        source_type: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict]:
        """班级×实验聚合视图查询"""
        with db_session() as s:
            sql = "SELECT * FROM v_class_summary WHERE 1=1"
            params: Dict[str, Any] = {}
            if class_name:
                sql += " AND class_name LIKE :c"
                params["c"] = f"%{class_name}%"
            if experiment_name:
                sql += " AND experiment_name LIKE :e"
                params["e"] = f"%{experiment_name}%"
            if source_type:
                sql += " AND source_type = :st"
                params["st"] = source_type
            sql += " ORDER BY class_name, experiment_name LIMIT :lim"
            params["lim"] = int(limit)
            rows = s.execute(text(sql), params).mappings().all()
            out = []
            for r in rows:
                d = dict(r)
                for k in ("avg_score", "weak_rate_percent", "min_score", "max_score", "median_score"):
                    if d.get(k) is not None:
                        d[k] = float(d[k])
                out.append(d)
            return out

    # ---------- 对话历史 & QA 沉淀 ----------
    @staticmethod
    def list_conversations(user_id: str = "default", limit: int = 200) -> List[Dict]:
        with db_session() as s:
            try:
                rows = s.execute(
                    text(
                        "SELECT id, title, user_id, pinned, created_at, updated_at "
                        "FROM conversations WHERE user_id = :u "
                        "ORDER BY pinned DESC, updated_at DESC LIMIT :l"
                    ),
                    {"u": user_id, "l": int(limit)},
                ).mappings().all()
                return [dict(r) for r in rows]
            except SQLAlchemyError:
                return []

    @staticmethod
    def save_conversation(conv: Dict) -> None:
        with db_session() as s:
            s.execute(text("""
                INSERT INTO conversations (id, title, user_id, pinned, created_at, updated_at)
                VALUES (:id, :title, :uid, :pin, NOW(), NOW())
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    pinned = EXCLUDED.pinned,
                    updated_at = NOW()
            """), {
                "id": conv["id"], "title": conv.get("title", "新对话"),
                "uid": conv.get("user_id", "default"),
                "pin": bool(conv.get("pinned", False)),
            })
            # messages 先全删再插（简化幂等）
            s.execute(text("DELETE FROM messages WHERE conversation_id = :cid"), {"cid": conv["id"]})
            msgs = conv.get("messages", [])
            if msgs:
                bulk = [
                    {"cid": conv["id"], "role": m.get("role", ""),
                     "content": m.get("content", ""), "tok": int(m.get("tokens", 0))}
                    for m in msgs
                ]
                s.execute(
                    text(
                        "INSERT INTO messages (conversation_id, role, content, tokens, created_at) "
                        "VALUES (:cid, :role, :content, :tok, NOW())"
                    ),
                    bulk,
                )
            s.commit()

    @staticmethod
    def get_conversation(cid: str) -> Optional[Dict]:
        with db_session() as s:
            c = s.execute(
                text("SELECT * FROM conversations WHERE id = :cid"), {"cid": cid}
            ).mappings().one_or_none()
            if not c:
                return None
            d = dict(c)
            ms = s.execute(
                text("SELECT role, content, tokens, created_at FROM messages "
                     "WHERE conversation_id = :cid ORDER BY id ASC"), {"cid": cid}
            ).mappings().all()
            d["messages"] = [dict(m) for m in ms]
            return d

    @staticmethod
    def delete_conversation(cid: str) -> None:
        with db_session() as s:
            s.execute(text("DELETE FROM conversations WHERE id = :cid"), {"cid": cid})
            s.commit()

    @staticmethod
    def save_qa(q: str, a: str, hit_knowledge: List[str], conv_id: str, embedding: List[float]) -> None:
        with db_session() as s:
            s.execute(
                text(
                    "INSERT INTO qa_sediment (user_question, assistant_reply, hit_knowledge, "
                    "conversation_id, embedding, created_at) "
                    "VALUES (:q, :a, :hk::text[], :cid, :emb::vector(512), NOW())"
                ),
                {"q": q, "a": a, "hk": list(hit_knowledge or []),
                 "cid": conv_id, "emb": list(embedding) if embedding else None},
            )
            s.commit()

    @staticmethod
    def retrieve_qa(query_embedding: List[float], top_k: int = 5) -> List[Dict]:
        with db_session() as s:
            rows = s.execute(text("""
                SELECT user_question, assistant_reply, hit_knowledge, created_at,
                       1 - (embedding <=> :emb::vector(512)) AS similarity
                FROM qa_sediment
                WHERE embedding IS NOT NULL
                ORDER BY similarity DESC LIMIT :lim
            """), {"emb": list(query_embedding), "lim": int(top_k)}).mappings().all()
            return [dict(r) for r in rows]


# ========================= 默认实例 =========================
pg_store = PgvectorStore()
