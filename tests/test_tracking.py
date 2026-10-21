"""Screening candidate -> tracked chemical -> detail view, with fake PubChem and Europe PMC."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pioneer import radar, research, screening, tracking  # noqa: E402
from pioneer.core import connect, init_db  # noqa: E402


def fake_identity(path, subject, name, request=None):
    cid = {'widgetol': 111, 'ambiguene': None}.get(name.lower(), 222)
    if cid is None:
        return dict(subject=subject, name=name, status='ambiguous', checked_at='x', source='fake',
                    response={'properties': [{'CID': 1}, {'CID': 2}]})
    status = 'not_found' if name.lower() == 'nothing' else 'unique_match'
    with connect(path) as db:
        db.execute('INSERT OR REPLACE INTO identities VALUES (?,?,?,?,?,?)', (subject, name, status,
                   json.dumps({'properties': [{'CID': cid, 'MolecularFormula': 'C1'}]}), 'x', 'fake'))
    return dict(subject=subject, name=name, status=status, checked_at='x', source='fake',
                response={'properties': [{'CID': cid, 'MolecularFormula': 'C1'}]} if status == 'unique_match' else {})


def fake_epmc(url):
    today = dt.date.today()
    rows = []
    for i in range(12):
        day = today - dt.timedelta(days=10 + i * 25)
        rows.append(dict(source='MED', id=str(9000 + i), title=f'Widgetol in river water and zebrafish embryos {i}',
                         abstractText='Widgetol was detected in surface water. Endocrine disruption in zebrafish larvae was measured.',
                         firstPublicationDate=day.isoformat(), pubTypeList={'pubType': ['research-article']}))
    return {'hitCount': len(rows), 'resultList': {'result': rows}, 'nextCursorMark': 'end'}


class TrackingBridge(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = str(Path(self.dir.name) / 'p.sqlite')
        init_db(self.db)
        with connect(self.db) as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('screening_only','true')")
            end = dt.date.today()
            rid = db.execute('''INSERT INTO screening_runs(query,start_date,end_date,started_at,status,rules_version)
                VALUES ('q',?,?,'x','complete','v')''', ((end - dt.timedelta(days=365)).isoformat(), end.isoformat())).lastrowid
            for cid, name, forms in [('cand-a', 'Widgetol', ['Widgetol', 'WGT']), ('cand-b', 'Ambiguene', ['Ambiguene'])]:
                data = dict(identifier=None, mentions=[dict(exact=f, record_key='MED:1', date=end.isoformat(),
                            section='title', name=name) for f in forms])
                db.execute('INSERT INTO screening_candidates VALUES (?,?,?,?,?)', (rid, cid, name, None, json.dumps(data)))
        self.rid = rid

    def tearDown(self):
        self.dir.cleanup()

    def test_names_skip_acronyms(self):
        with connect(self.db) as db:
            row = tracking._candidate(db, 'cand-a')
        self.assertEqual(tracking.names_to_try(row), ['Widgetol'])

    @mock.patch.object(radar, 'lookup_identity', fake_identity)
    @mock.patch.object(research, 'get_json', fake_epmc)
    def test_pick_collect_and_view(self):
        result = tracking.from_screening(self.db, {'candidate_id': 'cand-a'}, launch=False)
        self.assertEqual(result['status'], 'added')
        cid = result['chemical_id']
        with connect(self.db) as db:
            job = json.loads(db.execute('SELECT request FROM refresh_jobs WHERE id=?', (result['job']['id'],)).fetchone()['request'])
        radar.refresh_worker(self.db, result['job']['id'], job)

        detail = tracking.detail(self.db, {'chemical': cid})
        self.assertEqual(detail['origin']['kind'], 'screening')
        self.assertEqual(detail['snapshot']['status'], 'complete')
        self.assertEqual(detail['trend']['total'], 12)
        self.assertTrue(detail['trend']['comparable'])
        labels = {i['label'] for g in detail['content'] for i in g['items']}
        self.assertTrue({'water', 'zebrafish', 'endocrine', 'embryo'} <= labels)
        self.assertEqual(detail['job']['status'], 'complete')

        # The screening table now shows the candidate as tracked.
        end = dt.date.today()
        view = screening.view(self.db, {'start': (end - dt.timedelta(days=365)).isoformat(), 'end': end.isoformat()})
        self.assertEqual(next(r for r in view['rows'] if r['id'] == 'cand-a')['tracked_chemical_id'], cid)

        # Picking it again reuses the same tracked chemical and its complete snapshot.
        again = tracking.from_screening(self.db, {'candidate_id': 'cand-a'}, launch=False)
        self.assertEqual((again['status'], again['chemical_id']), ('existing', cid))

    @mock.patch.object(radar, 'lookup_identity', fake_identity)
    def test_ambiguous_not_added_then_manual_name(self):
        result = tracking.from_screening(self.db, {'candidate_id': 'cand-b'}, launch=False)
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['tried'][0]['status'], 'ambiguous')
        with connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM chemicals WHERE name='Ambiguene'").fetchone())
        fixed = tracking.from_screening(self.db, {'candidate_id': 'cand-b', 'name': 'ambiguene sodium'}, launch=False)
        self.assertEqual(fixed['status'], 'added')

    def test_pubchem_down_is_not_a_naming_problem(self):
        def down(path, subject, name, request=None):
            return dict(subject=subject, name=name, status='failed', response={}, checked_at='x', source='fake')
        with mock.patch.object(radar, 'lookup_identity', down):
            result = tracking.from_screening(self.db, {'candidate_id': 'cand-a'}, launch=False)
        self.assertEqual(result['status'], 'lookup_failed')
        self.assertEqual(len(result['tried']), 1)

    def test_unknown_candidate(self):
        with self.assertRaises(ValueError):
            tracking.from_screening(self.db, {'candidate_id': 'nope'}, launch=False)


if __name__ == '__main__':
    unittest.main()
