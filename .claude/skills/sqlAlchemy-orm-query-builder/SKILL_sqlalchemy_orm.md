# SKILL: SQLAlchemy ORM — Compact Reference (2.0 / 2.1)

> Always use 2.0-style APIs. Legacy `Query` object is deprecated. Use `select()` + `Session.execute()` / `Session.scalars()`.

---

## SETUP

```python
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

class Base(DeclarativeBase): pass

# Async (project standard)
async_engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/db", echo=True)
AsyncSessionLocal = async_sessionmaker(bind=async_engine, expire_on_commit=False)
```

---

## MODELS

```python
from typing import List, Optional
from datetime import datetime
from sqlalchemy import String, Integer, ForeignKey, Text, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

class User(Base):
    __tablename__ = "users"
    id:         Mapped[int]           = mapped_column(primary_key=True)
    name:       Mapped[str]           = mapped_column(String(100), index=True)
    email:      Mapped[str]           = mapped_column(String(255), unique=True)
    bio:        Mapped[Optional[str]] = mapped_column(Text, deferred=True)  # lazy column
    created_at: Mapped[datetime]      = mapped_column(server_default=func.now())
    posts: Mapped[List["Post"]] = relationship(back_populates="author", cascade="all, delete-orphan")

class Post(Base):
    __tablename__ = "posts"
    id:      Mapped[int] = mapped_column(primary_key=True)
    title:   Mapped[str] = mapped_column(String(200))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    author:  Mapped["User"] = relationship(back_populates="posts")
```

**Key column types:** `Integer`, `BigInteger`, `String(n)`, `Text`, `Boolean`, `Float`, `Numeric(p,s)`, `DateTime`, `JSON`, `LargeBinary`

**Column options:** `primary_key`, `nullable=False`, `unique`, `index`, `default=`, `server_default=`, `onupdate=`, `deferred=True`

---

## SESSION PATTERNS

```python
# Async (project standard)
async def example():
    async with AsyncSessionLocal() as session:
        async with session.begin():  # auto-commit, auto-rollback on exception
            session.add(User(name="Bob", email="b@b.com"))

# Key methods:
# session.add(obj) / session.add_all([...])
# session.get(Model, pk)          → identity map cache
# session.flush()                 → send SQL, don't commit
# session.commit() / rollback()
# session.refresh(obj)            → reload from DB
# session.execute(stmt)           → any statement
# session.scalars(stmt)           → ORM objects directly
```

> `expire_on_commit=False` — prevents expiry after commit; important for async and returning objects.

---

## CRUD

```python
from sqlalchemy import select, update, delete

# CREATE
user = User(name="Alice", email="a@a.com")
session.add(user)
session.commit(); session.refresh(user)

# READ — all
users = session.scalars(select(User)).all()
# READ — by PK
user = session.get(User, 1)
# READ — filtered
stmt = select(User).where(User.name == "Alice").limit(10).offset(0)
user = session.scalars(stmt).first()
# COUNT
count = session.scalar(select(func.count()).select_from(User))

# UPDATE — bulk ORM (2.0 style)
session.execute(update(User).where(User.id == 1).values(name="Alice Smith"))
session.commit()

# DELETE — bulk
session.execute(delete(User).where(User.id == 1))
session.commit()
```

---

## QUERYING

```python
from sqlalchemy import select, and_, or_, not_, func, text

.where(User.age >= 18)
.where(User.name.in_(["Alice", "Bob"]))
.where(User.bio.is_(None))           # IS NULL
.where(User.name.ilike("%ali%"))     # case-insensitive
.where(and_(User.age > 18, User.active == True))
.where(or_(User.role == "admin", User.role == "mod"))

# JOINs
stmt = select(User, Post).join(Post, Post.user_id == User.id)
stmt = select(User).join(User.posts)
stmt = select(User).outerjoin(User.posts)

# GROUP BY + HAVING
stmt = (select(User.name, func.count(Post.id).label("post_count"))
    .join(User.posts).group_by(User.name).having(func.count(Post.id) > 5))

# Pagination
stmt = select(User).order_by(User.id).limit(20).offset(page * 20)

# Result consumption
session.scalars(stmt).all()          # list of ORM objects
session.scalars(stmt).first()        # first or None
session.scalars(stmt).one()          # exactly one (raises if 0 or >1)
session.execute(stmt).all()          # list of Row tuples
session.scalar(stmt)                 # single scalar
```

---

## RELATIONSHIPS & LOADING

```python
from sqlalchemy.orm import selectinload, joinedload, contains_eager, raiseload

# selectinload: separate SELECT IN — best for collections
stmt = select(User).options(selectinload(User.posts))

# joinedload: single JOIN — best for to-one; requires .unique() for collections
users = session.scalars(select(User).options(joinedload(User.profile))).unique().all()

# Nested
stmt = select(Post).options(selectinload(Post.author).selectinload(User.posts))

# contains_eager: you write JOIN, SA uses it for loading
stmt = (select(User).join(User.posts).where(Post.published == True)
        .options(contains_eager(User.posts)))

# raiseload: fail-fast on lazy access (dev N+1 guard)
stmt = select(User).options(raiseload("*"))
```

**Rule:** Lazy loading is **broken in async** — always eager load. Use `AsyncAttrs` mixin for escape hatch:
```python
from sqlalchemy.ext.asyncio import AsyncAttrs
class Base(AsyncAttrs, DeclarativeBase): pass
# then: await obj.awaitable_attrs.posts
```

| Scenario | Use |
|---|---|
| To-one (scalar) | `joinedload` |
| To-many (collection) | `selectinload` |
| Already joining for filter | `contains_eager` |
| Dev/testing N+1 guard | `raiseload("*")` |

---

## COMMON PATTERNS

```python
# UPSERT
user = session.merge(User(id=1, name="Updated"))
session.commit()

# BULK INSERT (fast)
session.execute(User.__table__.insert(), [{"name": "A", "email": "a@a.com"}, ...])
session.commit()

# HYBRID PROPERTY
from sqlalchemy.ext.hybrid import hybrid_property
class User(Base):
    @hybrid_property
    def full_name(self): return f"{self.first_name} {self.last_name}"
    @full_name.expression
    def full_name(cls): return cls.first_name + " " + cls.last_name

# EVENTS (MASTER_KEY injection pattern used in db_layer/connection.py)
from sqlalchemy import event
@event.listens_for(engine, "connect")
def set_guc(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("SELECT set_config('app.master_key', %s, false)", (key,))
    cursor.close()
```

---

## ASYNC PATTERN (FastAPI / MCP style)

```python
from sqlalchemy.ext.asyncio import AsyncSession

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session

async def get_user(user_id: int, db: AsyncSession):
    stmt = select(User).where(User.id == user_id).options(selectinload(User.posts))
    return await db.scalar(stmt)
```

---

## GOTCHAS

| Trap | Fix |
|---|---|
| Lazy loading in async | Always `selectinload` / `joinedload` upfront |
| `DetachedInstanceError` | Load relationships inside session; `expire_on_commit=False` |
| N+1 queries | `selectinload`; `raiseload("*")` in dev |
| `joinedload` on collections dupes | `.unique()` on result |
| `session` not thread-safe | Never share across threads |
| Bulk ops slow | `session.execute(table.insert(), rows_list)` |

---

## PERFORMANCE

- `echo=True` in dev — inspect SQL
- `selectinload` not `joinedload` for collections (avoids Cartesian product)
- `deferred=True` for large text/binary not always needed
- Pool: `pool_size=10, max_overflow=20, pool_pre_ping=True`
- Count: `select(func.count()).select_from(Model)` — never `len(scalars(select(Model)).all())`
- PK lookup: `session.get(Model, pk)` — hits identity map cache

---

## QUERY BUILDER PATTERN — Composable, Chainable, Dispatchable

> Production pattern from record-list service: UNION ALL base subquery + pre-filtered aggregate JOINs + per-`countType` dispatch.
> Philosophy: **each modifier is a pure function `stmt → stmt`**.

### 1. Small Modifier Blocks

```python
def apply_program_filter(stmt, program_state, self_deactivated=False):
    if program_state != "all":
        stmt = stmt.where(User.program_active == program_state)
    if self_deactivated:
        stmt = stmt.where(User.self_deactivated == True)
    return stmt

def apply_sort_and_pagination(stmt, sort_name, sort_dir, limit=None, offset=None):
    col_map = {"name": User.name, "uniqueId": User.unique_id, "scope": Scope.name}
    if sort_name in col_map:
        col = col_map[sort_name]
        stmt = stmt.order_by(col.asc() if sort_dir == "asc" else col.desc())
    if limit:
        stmt = stmt.limit(limit).offset(offset or 0)
    return stmt
```

### 2. Pre-Filter Before Joining (the optimization)

**Problem:** joining large table pulls all rows, then filters. **Fix:** subquery first — JOIN operates on reduced dataset.

```python
now = datetime.now(timezone.utc)

next_appt_subq = (
    select(Appointment.user_id, func.min(Appointment.time).label("next_appointment_date"))
    .where(Appointment.time > now)
    .where(Appointment.status != "CANCELED")
    .where(Appointment.virtual_encounter == True)
    .group_by(Appointment.user_id)
    .subquery("record_events")
)

def join_appointment_aggregates(stmt):
    return stmt.outerjoin(next_appt_subq, next_appt_subq.c.user_id == User.id)
```

**Why:** `GROUP BY` inside subquery reduces rows before join. With 164k records + multiple aggregate joins → hash join instead of sequential scan.

### 3. Count Wrapping

```python
def build_count_query(base_stmt):
    subq = base_stmt.subquery("base")
    return select(func.count().label("count")).select_from(subq)

def build_program_count_query(base_stmt):
    subq = subq = base_stmt.subquery()
    return select(
        func.count().label("count"),
        func.sum(case((subq.c.program_active == 1, 1), else_=0)).label("active"),
        func.sum(case((subq.c.program_active == 0, 1), else_=0)).label("inactive"),
    ).select_from(subq)
```

### 4. Dispatch Map

```python
COUNT_TYPE_MAP = {
    "fetchData": lambda stmt, ctx: apply_program_filter(
        apply_sort_and_pagination(
            apply_group_by(apply_filter_options(join_record_info_aggregates(join_appointment_aggregates(join_contacts(stmt))), ctx)),
            ctx.sort_name, ctx.sort_dir, ctx.limit, ctx.offset
        ), ctx.program_state, ctx.self_deactivated
    ),
    "filterCount": lambda stmt, ctx: build_program_count_query(apply_filter_options(join_contacts(stmt), ctx)),
    "totalCount": lambda stmt, _ctx: build_count_query(stmt),
}

# Usage
def list_records(session, count_type, project_id, scope_ids=None, ctx=None):
    base = build_base_stmt(project_id, scope_ids)
    stmt = COUNT_TYPE_MAP[count_type](base, ctx)
    return session.execute(stmt).all()
```

**Or use `pipe` for readability:**
```python
from functools import reduce
def pipe(stmt, *fns): return reduce(lambda s, f: f(s), fns, stmt)
```

| Pattern | When |
|---|---|
| Modifier functions `stmt → stmt` | Any reused filter/join |
| Pre-filter subquery before JOIN | Aggregate tables (appointments, logins) |
| `build_count_query(base_stmt)` | Count + data from same filters |
| Dispatch map `count_type → chain` | One endpoint, multiple query shapes |

---

## Dropped
- Setup: cut SQLite/MySQL formats (not used in project)
- Column type cheatsheet: cut to inline list (table was 12 rows of self-evident content)
- Session patterns: cut FastAPI dependency injection example (not used)
- Session methods table: redundant with code comments
- Relationships loading strategy table: redundant with the strategy guide kept above
- Alembic section: deleted entirely (covered by ALEMBIC_SKILL.md)
- Async full example: compressed into existing async pattern section
- Query Builder pipe helper: kept concept, cut duplicate lambda examples
- All `# ──────` comment banners: visual noise
