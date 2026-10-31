from pathlib import Path
import sqlite3
import subprocess
import types
from storage import Database
from workshop import Workshop

def test_upgrade_real_v030_schema_and_restore_permissions(tmp_path):
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as initial:
        initial.executescript(Path('tests/fixtures/v030-schema.sql').read_text(encoding='utf-8'))
    initial.close()
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO rooms(code,title,token,created) VALUES('ABC234','旧版本','host-secret',1)")
        c.execute("INSERT INTO people(id,room,name,token,seen,joined) VALUES('p','ABC234','旧成员','person-secret',2,1)")
        c.execute("INSERT INTO steps(id,room,title,created,phase,opened,position) VALUES('s','ABC234','旧步骤',2,'active',2,0)")
        c.execute("INSERT INTO statuses(step,person,state,updated) VALUES('s','p','done',3)")
        c.execute("INSERT INTO roles(id,room,name,token,role) VALUES('helper','ABC234','旧助教','helper-secret','helper')")
    c.close()
    new=Database(path);service=Workshop(new)
    assert service.snapshot('ABC234','host-secret')['steps'][0]['counts']['done']==1
    assert service.snapshot('ABC234','person-secret')['me']['environment']=='unspecified'
    assert service.snapshot('ABC234','helper-secret')['role']=='helper'
    backup=tmp_path/'restored.sqlite3';new.backup(backup)
    restored=Workshop(Database(backup))
    assert restored.snapshot('ABC234','person-secret')['steps'][0]['mine']=='done'
    assert restored.snapshot('ABC234','helper-secret')['role']=='helper'
