-- Every utterance (D26). Never purged: with the model id on each tier-2
-- row this is the regression fixture for future model changes.
CREATE TABLE voice_utterances (
    id          TEXT PRIMARY KEY,
    heard_at    TEXT NOT NULL,
    text        TEXT NOT NULL,
    tier        INTEGER NOT NULL CHECK (tier IN (0, 1, 2)),
    model       TEXT,
    intent_json TEXT NOT NULL,
    outcome     TEXT NOT NULL
);
CREATE INDEX idx_utterances_heard ON voice_utterances(heard_at DESC);
CREATE INDEX idx_utterances_tier ON voice_utterances(tier, heard_at DESC);
