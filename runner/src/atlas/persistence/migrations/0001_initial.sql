-- Initial schema. All timestamps are ISO8601 UTC text. Money is integer
-- micro-dollars (never floats). Approval state transitions happen via
-- conditional UPDATE ... WHERE state='pending' (D16 idempotency).

CREATE TABLE job_runs (
    run_id        TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL,
    tier          INTEGER NOT NULL CHECK (tier IN (1, 2, 3)),
    mode          TEXT NOT NULL CHECK (mode IN ('read', 'propose', 'write')),
    state         TEXT NOT NULL CHECK (state IN
                    ('running', 'completed', 'failed', 'awaiting_approval', 'timed_out')),
    attempt       INTEGER NOT NULL DEFAULT 1,
    scheduled_for TEXT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    report_json   TEXT,
    error         TEXT
);

CREATE INDEX idx_runs_job_recent ON job_runs(job_id, started_at DESC);
CREATE INDEX idx_runs_state ON job_runs(state);

CREATE TABLE approvals (
    approval_id     TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES job_runs(run_id),
    job_id          TEXT NOT NULL,
    action_json     TEXT NOT NULL,  -- the frozen payload, executed verbatim (D16)
    state           TEXT NOT NULL CHECK (state IN ('pending', 'approved', 'rejected', 'expired')),
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    decided_at      TEXT,
    decision_source TEXT
);

CREATE INDEX idx_approvals_state ON approvals(state);

CREATE TABLE budget_ledger (
    entry_id        TEXT PRIMARY KEY,
    run_id          TEXT REFERENCES job_runs(run_id),
    job_id          TEXT,
    model           TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    cost_usd_micros INTEGER NOT NULL,
    recorded_at     TEXT NOT NULL
);

CREATE INDEX idx_ledger_recorded ON budget_ledger(recorded_at);
