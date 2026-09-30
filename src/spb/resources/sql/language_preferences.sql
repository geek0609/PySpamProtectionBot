CREATE TABLE IF NOT EXISTS language_preferences
(
    preference_type TEXT NOT NULL,
    preference_id   INTEGER NOT NULL,
    language        TEXT    NOT NULL DEFAULT 'en',
    PRIMARY KEY (preference_type, preference_id)
);