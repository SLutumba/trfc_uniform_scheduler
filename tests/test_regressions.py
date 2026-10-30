"""Regression coverage for defects found during the 30 October review."""
import csv
import io
import runpy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from test_app import env, setup


def test_production_rejects_ephemeral_database(env, monkeypatch, tmp_path):
    mod, _ = env
    database = tmp_path / 'must-not-be-created.db'
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(database))
    with pytest.raises(RuntimeError, match='PostgreSQL DATABASE_URL'):
        runpy.run_path(mod.__file__)
    assert not database.exists()


def test_production_requires_separate_strong_secrets(env, monkeypatch):
    mod, _ = env
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unreachable.invalid/trfc')
    monkeypatch.setenv('SETUP_KEY', 'x' * 48)
    monkeypatch.delenv('ATTENDANCE_SIGNING_KEY', raising=False)
    with pytest.raises(RuntimeError, match='strong ATTENDANCE_SIGNING_KEY'):
        runpy.run_path(mod.__file__)


def protocol(client):
    return next(t for t in client.get('/api/teams').json() if t['slug'] == 'protocol')


def test_duplicate_administrator_created_member_is_conflict(env):
    _, client = env
    headers = {'x-csrf-token': setup(client)}
    body = {'email': 'member@example.test', 'password': 'temporary-password-123',
            'teams': [protocol(client)['id']]}
    assert client.post('/api/users', json=body, headers=headers).status_code == 200
    assert client.post('/api/users', json=body, headers=headers).status_code == 409


def test_private_responses_are_not_cached(env):
    _, client = env
    setup(client)
    for path in ['/api/me', '/api/users', '/api/my-schedule', '/api/public/missing', '/']:
        assert client.get(path).headers.get('cache-control') == 'no-store', path


def test_unavailable_mandatory_outfit_blocks_preview_and_publication(env):
    _, client = env
    headers = {'x-csrf-token': setup(client)}
    team = protocol(client)
    original = client.get(f'/api/teams/{team["id"]}').json()
    config = original['config']
    config['settings']['startMonth'] = '2026-11'
    next(c for c in config['palette']['ties'] if c['id'] == 't-black')['confirmed'] = False
    assert client.put(f'/api/teams/{team["id"]}/config', headers=headers,
                      json={'version': original['version'], 'config': config}).status_code == 200
    for path, method in [('preview', client.get), ('publish', client.post)]:
        response = method(f'/api/teams/{team["id"]}/{path}', headers=headers)
        assert response.status_code == 422
        assert '2026-11-29' in response.json()['detail']
    assert client.get('/api/public/protocol').status_code == 404


def test_conflicting_rules_fail_but_date_override_has_precedence(env):
    mod, client = env
    setup(client)
    config = client.get(f'/api/teams/{protocol(client)["id"]}').json()['config']
    config['settings'] = {'startMonth': '2026-11', 'months': 1, 'includeFridays': False}
    config['rules'].append({'id': 'conflict', 'kind': 'last', 'weekday': 0,
                            'name': 'Other month end', 'outfitId': 'c-2'})
    with pytest.raises(mod.HTTPException) as error:
        mod.generate(mod.validate_config(config))
    assert error.value.status_code == 422
    config['rules'].append({'id': 'override', 'kind': 'date', 'date': '2026-11-29',
                            'name': 'Conference', 'outfitId': 'c-3'})
    row = next(r for r in mod.generate(mod.validate_config(config)) if r['date'] == '2026-11-29')
    assert row['outfit']['id'] == 'c-3'


@pytest.mark.parametrize('bad_labels', [[], None, 'labels'])
def test_malformed_labels_return_validation_error(env, bad_labels):
    _, client = env
    headers = {'x-csrf-token': setup(client)}
    team = protocol(client)
    original = client.get(f'/api/teams/{team["id"]}').json()
    original['config']['labels'] = bad_labels
    response = client.put(f'/api/teams/{team["id"]}/config', headers=headers,
                          json={'version': original['version'], 'config': original['config']})
    assert response.status_code == 422


def test_login_limit_uses_normalized_email(env):
    _, client = env
    setup(client)
    for attempt in range(8):
        response = client.post('/api/login', json={'email': ' ' * attempt + 'ADMIN@TRFC.TEST',
                                                   'password': 'wrong-password'})
        assert response.status_code == 401
    assert client.post('/api/login', json={'email': 'admin@trfc.test',
                                           'password': 'secure-password-123'}).status_code == 429


def test_unicode_setup_secret_fails_safely(env):
    _, client = env
    assert client.post('/api/setup', json={'email': 'a@b.test', 'password': 'test-password-123',
                                          'setup_key': 'cl\u00e9'}).status_code == 403


def test_utc_timestamps_handle_postgres_and_sqlite(env):
    mod, _ = env
    expected = '2026-11-01T06:29:59Z'
    assert mod.utc_iso(datetime(2026, 11, 1, 6, 29, 59)) == expected
    assert mod.utc_iso(datetime(2026, 11, 1, 6, 29, 59, tzinfo=timezone.utc)) == expected
    assert mod.utc_iso(datetime(2026, 11, 1, 8, 29, 59,
                                tzinfo=ZoneInfo('Africa/Johannesburg'))) == expected


def test_csv_sanitizes_custom_headers(env):
    mod, _ = env
    output = mod.csv_schedule({'labels': {'suits': '=HYPERLINK(1)'}, 'rows': []})
    assert list(csv.reader(io.StringIO(output.lstrip('\ufeff'))))[0][2] == "'=HYPERLINK(1)"


@pytest.mark.parametrize('hour,minute,second,status', [
    (8, 29, 59, 'on_time'), (8, 30, 0, 'late'),
    (14, 59, 59, 'late'), (15, 0, 0, 'absent'),
])
def test_attendance_exact_boundaries(env, hour, minute, second, status):
    mod, _ = env
    assert mod.attendance_status(datetime(2026, 11, 1, hour, minute, second,
                                          tzinfo=ZoneInfo('Africa/Johannesburg'))) == status


def test_sqlite_enforces_foreign_keys(env):
    mod, _ = env
    with mod.engine.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA foreign_keys').scalar() == 1


def test_unconfirmed_main_garment_is_not_scheduled(env):
    mod, client = env
    setup(client)
    config = client.get(f'/api/teams/{protocol(client)["id"]}').json()['config']
    next(c for c in config['palette']['suits'] if c['id'] == 's-navy')['confirmed'] = False
    rows = mod.generate(mod.validate_config(config))
    assert all(not row['outfit'] or row['outfit']['parts']['suits']['name'] != 'Navy Blue'
               for row in rows)


def test_failed_publication_preserves_previous_snapshot(env):
    _, client = env
    headers = {'x-csrf-token': setup(client)}
    team = protocol(client)
    assert client.post(f'/api/teams/{team["id"]}/publish', headers=headers).status_code == 200
    published = client.get('/api/public/protocol').json()
    draft = client.get(f'/api/teams/{team["id"]}').json()
    next(c for c in draft['config']['palette']['ties'] if c['id'] == 't-black')['confirmed'] = False
    assert client.put(f'/api/teams/{team["id"]}/config', headers=headers,
                      json={'version': draft['version'], 'config': draft['config']}).status_code == 200
    assert client.post(f'/api/teams/{team["id"]}/publish', headers=headers).status_code == 422
    assert client.get('/api/public/protocol').json() == published
