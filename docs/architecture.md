# OrisunIwadi architecture

Solid boxes are built. Dashed boxes are planned.

## 1. Code structure: clean architecture

Imports only point inward. `domain` and `application` contain no framework or I/O code;
`tests/unit/test_architecture.py` enforces both rules.

```mermaid
flowchart TB
    subgraph ENTRY["entrypoints (frameworks)"]
        DAG["dagster/<br/>assets · schedules · resources"]
        API["api/<br/>FastAPI routes, JWT roles"]
    end
    subgraph ADAPT["adapters (I/O)"]
        SRC["sources/<br/>gdc · cbioportal · clinicaltrials · cdc · civic"]
        S3A["storage/s3_raw_store.py"]
        PGA["storage/postgres_store.py"]
    end
    subgraph APP["application (use cases)"]
        UC["IngestDataset, DatasetQueries,<br/>Authenticate"]
        PORTS["ports:<br/>Source · RawSnapshotStore · CuratedStore"]
    end
    subgraph DOM["domain (rules)"]
        ENT["Dataset entity<br/>hashing · content-addressed keys · de-dupe"]
    end

    DAG --> UC
    DAG --> SRC & S3A & PGA
    API --> UC
    SRC -. implements .-> PORTS
    S3A -. implements .-> PORTS
    PGA -. implements .-> PORTS
    UC --> PORTS
    UC --> ENT
    PORTS --> ENT

    classDef planned stroke-dasharray: 5 5
```

The FastAPI app follows the same rule: routes in `entrypoints/api/`, use cases
(`DatasetQueries`, `Authenticate`) in `application/`, roles in `domain/access.py`, and the
read store, user store, password hashing and JWT code in `adapters/`.

## 2. Data flow

```mermaid
flowchart LR
    subgraph SRC["Public sources"]
        GDC["NCI GDC"]
        CBIO["cBioPortal"]
        CT["ClinicalTrials.gov"]
        CDC["CDC indicators"]
        CIVIC["CIViC"]
    end

    subgraph RUN["IngestDataset (one Dagster asset per dataset, daily 06:00 ET)"]
        direction TB
        FETCH["1. Fetch<br/>paging, retries with backoff"]
        SNAP["2. Raw snapshot<br/>canonical JSONL, sha256"]
        MAP["3. Map<br/>typed columns"]
        DQ["4. Validate + de-dupe<br/>type + business rules"]
        UPSERT["5. Upsert<br/>skip rows whose hash is unchanged"]
        FETCH --> SNAP --> MAP --> DQ --> UPSERT
    end

    SRC --> FETCH
    SNAP -->|"put if new"| S3[("S3 raw zone (bronze)<br/>SeaweedFS locally")]
    UPSERT --> PG[("PostgreSQL (silver)<br/>schema oncology")]
    DQ -->|failed rows + reasons| QUAR[("quality.quarantine")]
    UPSERT -->|"every run"| CAT[("catalog.runs<br/>catalog.datasets")]
    PG --> APIS["FastAPI, role-based"]

    classDef planned stroke-dasharray: 5 5
```

Every curated row stores `_record_hash`, `_raw_s3_uri` (the raw file it came from),
`_run_id` (the run that last inserted or changed it), `_first_seen_at` and `_last_updated_at`.

### Metadata catalog (provenance)

| Table | One row per | Holds |
|---|---|---|
| `catalog.datasets` | dataset | description, source URL, columns, primary key, quality rules |
| `catalog.runs` | run × dataset | status, start/end time, code version (git commit), source URL, raw file sha256 + S3 URI, row counts, error if it failed |

Failed runs are logged too, with the error. A Dagster retry of the same run replaces its row.
Code version is `GIT_SHA` on AWS (set by CI) or the local commit, with `-dirty` when there
are uncommitted changes.

Tracing a row back to its source: row `_run_id` → `catalog.runs` → `raw_s3_uri` (the exact
raw file) and `code_version` (the exact code that loaded it).

### Concurrency

| Risk | Guard |
|---|---|
| Two runs load the same dataset at once (e.g. schedule + manual) and an older fetch overwrites newer data | Per-dataset Postgres advisory lock, held from fetch to commit. A second run gets `DatasetAlreadyRunning`; Dagster retries it after 30 s. Postgres frees the lock if the process dies. |
| Two runs create the same table at once on a fresh database | Table setup runs under one app-wide advisory lock |
| Table setup blocking readers during a load (`ALTER TABLE` and `CREATE INDEX` lock even when there is nothing to do) | DDL runs only when a table or column is actually missing, and commits right away |
| A step fails half-way | Each store writes in one transaction; a retry re-runs safely because every write is idempotent |

### Data quality

Each row is checked before it is loaded. Rows that fail go to `quality.quarantine` with the
reasons, and the rest of the batch still loads.

| Rule kind | Where defined | Example |
|---|---|---|
| Primary key present | automatic | `case_id is required` |
| Column type | automatic, from each column's Postgres type | `evidence_id='abc' is not a valid integer` |
| Business rule | `rules=` on each dataset in `adapters/sources/` | `evidence_rating=7 outside [1, 5]` |

Quarantined rows are keyed by `(dataset, record_hash)`, so a bad row seen again is not added
twice. Each asset has two Dagster asset checks: `quarantine_rate_ok` (warns above 5%) and
`table_not_empty` (error).

### Fault tolerance

Goal: a bug or an outage in one part degrades that part only, never corrupts data, and the
system recovers without manual restarts. Each row below has a test that makes the failure
happen (`tests/integration/test_fault_tolerance.py`, `tests/unit/test_api.py`).

| Failure | What happens |
|---|---|
| Bug or outage in one source | Only that dataset's step fails; the other datasets load. Transient errors are retried. |
| Buggy transform produces wrong-but-valid rows | Safety brake: a run that quarantines over 20% of records, would change over 50% of existing rows, or fetches 0 records when data exists is rejected and rolled back. The last good data stays; the run is logged as failed with the reason. Override once with `allow_large_changes: true`. |
| Crash mid-load | Each write is one transaction, so nothing half-written is left. The run lock is released by Postgres when the connection drops. |
| Database down or slow | API stays up and answers 503 + `Retry-After` within ~3 s; `/ready` reports 503 so a load balancer routes away; it recovers by itself when the database is back. |
| Slow or heavy API query | Cut off after 5 s (set on the database user and the connection). At most 10 connections per API copy and 20 queued requests; beyond that, instant 503. |
| One side floods the database | Separate database users with separate connection limits (20 each), so the API can't starve the pipeline or the reverse. |
| Bug in the API | The API's database user is read-only; it cannot change data. The pipeline's user cannot read logins. |

Database users (`local/db-roles.sql`, applied by `make up`):

| User | Access |
|---|---|
| `orisun_etl` | owns `oncology`, `quality`, `catalog`; no access to `auth` |
| `orisun_api` | read-only on those schemas and `auth.users`; 5 s statement timeout |
| admin | schema setup, user management, tests |

Still single points of failure locally (one Postgres, one machine); on AWS these are covered by
RDS Multi-AZ, 2+ API tasks across Availability Zones, and S3 (see section 3).

### API access control

| Role | Can use |
|---|---|
| `viewer` | dataset list, summaries, grouped counts (groups under 10 rows hidden) |
| `researcher` | + row-level data with `?column=value` filters and paging |
| `admin` | + quarantine and run log |

- Login (`POST /auth/token`) returns a 30-minute JWT (HS256, algorithm pinned, issuer checked).
- Passwords are hashed with scrypt; unknown users take as long to reject as wrong passwords.
- Every API database session is read-only, so the API cannot change data even if it has a bug.
- Dataset and column names from the URL are checked against the dataset definitions before any
  SQL is built; values are always bound parameters.

## 3. AWS deployment (planned)

```mermaid
flowchart TB
    GHA["GitHub Actions<br/>test, terraform plan, build"] -->|OIDC| AWS
    subgraph AWS["AWS · us-east-1"]
        subgraph VPC["VPC, 2 Availability Zones"]
            ALB["Load balancer + WAF<br/>(public subnet)"]
            NAT["NAT gateway<br/>(public subnet)"]
            DAGSTER["Dagster on ECS Fargate<br/>role: etl"]
            APISVC["FastAPI on ECS Fargate<br/>role: api"]
            RDS[("RDS PostgreSQL")]
        end
        S3B[("S3 raw bucket")]
        SM["Secrets Manager"]
        CW["CloudWatch"]
        ECR["ECR"]
    end
    USERS["API users"] -->|HTTPS + JWT| ALB --> APISVC --> RDS
    DAGSTER --> RDS
    DAGSTER --> S3B
    DAGSTER --> NAT --> PUB["Public APIs"]
    ECR --> DAGSTER & APISVC
    DAGSTER & APISVC -.-> SM & CW
```

Least privilege: the ETL role can write only to `raw/*` in the raw bucket and read its own
database secret. The API role has no S3 access and connects as a read-only database user.

## 4. Build status

| Phase | Scope | Status |
|---|---|---|
| 1. Local setup | uv, Docker Compose (Postgres + SeaweedFS), Dagster | Done |
| 2. ETL | 5 datasets → S3 + Postgres, idempotent, clean architecture | Done |
| 3. Data quality | Validation, quarantine table, Dagster asset checks | Done |
| 4. Metadata catalog | Provenance for every run, row-level lineage | Done |
| 5. API | FastAPI + JWT roles, read-only DB sessions | Done |
| 6. AWS | Terraform: VPC, S3, RDS, ECS, IAM | Next |
| 7. CI/CD | GitHub Actions | Planned |
