-- Schema captured from commit 92f158a (v0.3.0). No user data.
CREATE TABLE rooms(code TEXT PRIMARY KEY,title TEXT,token TEXT,created REAL,closed INTEGER DEFAULT 0, expected INTEGER DEFAULT 0, revision INTEGER DEFAULT 0, announcement TEXT DEFAULT '', timer_end REAL DEFAULT 0);
CREATE TABLE people(id TEXT PRIMARY KEY,room TEXT REFERENCES rooms(code) ON DELETE CASCADE,name TEXT,token TEXT,seen REAL,joined REAL, left_at REAL, seat TEXT DEFAULT '');
CREATE TABLE steps(id TEXT PRIMARY KEY,room TEXT REFERENCES rooms(code) ON DELETE CASCADE,title TEXT,created REAL, criteria TEXT DEFAULT '', link TEXT DEFAULT '', reasons TEXT DEFAULT '[]', tips TEXT DEFAULT '', phase TEXT DEFAULT 'active', position INTEGER DEFAULT 0, opened REAL, frozen TEXT);
CREATE TABLE statuses(step TEXT REFERENCES steps(id) ON DELETE CASCADE,person TEXT REFERENCES people(id) ON DELETE CASCADE,state TEXT,updated REAL, version INTEGER DEFAULT 1,PRIMARY KEY(step,person));
CREATE TABLE issues(id TEXT PRIMARY KEY,step TEXT REFERENCES steps(id) ON DELETE CASCADE,title TEXT,resolved INTEGER DEFAULT 0, stage TEXT DEFAULT 'open', assignee TEXT DEFAULT '', merged_into TEXT, created REAL DEFAULT 0, version INTEGER DEFAULT 1);
CREATE TABLE votes(issue TEXT REFERENCES issues(id) ON DELETE CASCADE,person TEXT REFERENCES people(id) ON DELETE CASCADE, active INTEGER DEFAULT 1, updated REAL DEFAULT 0, version INTEGER DEFAULT 1,PRIMARY KEY(issue,person));
CREATE TABLE roles(id TEXT PRIMARY KEY,room TEXT REFERENCES rooms(code) ON DELETE CASCADE,name TEXT,token TEXT,role TEXT,revoked INTEGER DEFAULT 0);
CREATE TABLE answers(id TEXT PRIMARY KEY,issue TEXT REFERENCES issues(id) ON DELETE CASCADE,body TEXT,link TEXT,public INTEGER,created REAL,author TEXT);
CREATE TABLE events(id INTEGER PRIMARY KEY,room TEXT REFERENCES rooms(code) ON DELETE CASCADE,kind TEXT,entity TEXT,actor TEXT,created REAL,detail TEXT);
CREATE TABLE requests(room TEXT REFERENCES rooms(code) ON DELETE CASCADE,actor TEXT,key TEXT,fingerprint TEXT,result TEXT,PRIMARY KEY(room,actor,key));
CREATE TABLE tails(step TEXT REFERENCES steps(id) ON DELETE CASCADE,person TEXT REFERENCES people(id) ON DELETE CASCADE,recovered INTEGER DEFAULT 0,updated REAL,PRIMARY KEY(step,person));
PRAGMA user_version=3;
