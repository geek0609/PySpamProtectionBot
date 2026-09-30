CREATE TABLE IF NOT EXISTS users
(
    id    INTEGER PRIMARY KEY,
    username   TEXT UNIQUE,
    first_name TEXT,
    last_name  TEXT
);
