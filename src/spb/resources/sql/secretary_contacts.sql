CREATE TABLE IF NOT EXISTS secretary_contacts
(
    business_connection_id TEXT NOT NULL,
    id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'UNKNOWN',
    first_seen_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (business_connection_id, id)
);

CREATE INDEX IF NOT EXISTS idx_secretary_contacts_status
    ON secretary_contacts (business_connection_id, status);
