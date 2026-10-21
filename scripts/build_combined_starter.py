#!/usr/bin/env python3
"""Combine sanitized tracked-chemical snapshots with literature-first screening data."""
import argparse
import gzip
import shutil
import sqlite3
import tempfile
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('screening_gzip',type=Path)
    parser.add_argument('tracked_database',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='pioneer-combined-') as folder:
        target=Path(folder)/'starter.sqlite'
        with gzip.open(args.screening_gzip,'rb') as source,target.open('wb') as dest:shutil.copyfileobj(source,dest)
        with sqlite3.connect(target) as db:
            db.execute('PRAGMA foreign_keys=ON');db.execute('ATTACH DATABASE ? AS tracked',(str(args.tracked_database.resolve()),))
            db.execute('INSERT OR REPLACE INTO chemicals SELECT id,name,terms FROM tracked.chemicals')
            db.execute('''INSERT OR REPLACE INTO runs(id,chemical_id,started_at,finished_at,status,query,source,start_year,end_year,
                source_total,fetched,unique_count,error,query_version)
                SELECT id,chemical_id,started_at,finished_at,status,query,source,start_year,end_year,source_total,fetched,
                unique_count,error,query_version FROM tracked.runs''')
            db.execute('''INSERT OR REPLACE INTO papers(run_id,record_key,source_id,title,authors,journal,year,date,date_basis,doi,pmid,abstract,pub_types,is_open_access)
                SELECT run_id,record_key,source_id,title,authors,journal,year,date,date_basis,doi,pmid,abstract,pub_types,is_open_access FROM tracked.papers''')
            old_tables={r[0] for r in db.execute("SELECT name FROM tracked.sqlite_master WHERE type='table'")}
            if 'identities' in old_tables:
                db.execute("INSERT OR REPLACE INTO identities SELECT * FROM tracked.identities WHERE subject LIKE 'chemical:%'")
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('screening_only','false')")
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('combined_workspace','true')")
            db.commit();db.execute('DETACH DATABASE tracked');db.execute('VACUUM')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with target.open('rb') as source,args.output.open('wb') as raw:
            with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as dest:shutil.copyfileobj(source,dest)
    print(args.output)


if __name__=='__main__':main()
