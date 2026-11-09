from datetime import datetime, timezone, timedelta
from plugin.todo_log import TodoLog
from plugin.todo_model import fold, resolve_due, resolve_snooze

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)

def test_add_close_and_idempotency(tmp_path):
    log = TodoLog(tmp_path)
    kw = dict(request_id='r1', now=NOW, tz=timezone.utc)
    a = log.add_todo('u', 'Buy milk', origin='user', **kw)
    assert a['id'] == 'T1'
    assert log.add_todo('u', 'Buy milk', origin='user', **kw) == a
    b = log.apply_action('u', 't1', 'done', by='user', how='command', request_id='r2', now=NOW, tz=timezone.utc)
    assert b['state'] == 'done'
    assert fold(log.read().events)['T1'].state == 'done'

def test_words_require_capture(tmp_path):
    log = TodoLog(tmp_path)
    log.add_todo('u', 'Buy milk', origin='user', request_id='a', now=NOW, tz=timezone.utc)
    result = log.apply_action('u', 'T1', 'done', by='agent', how='words', user_words='done', shown_ids=['T1'], request_id='b', now=NOW, tz=timezone.utc)
    assert not result['ok']

def test_time_helpers():
    assert resolve_due('tomorrow', NOW, timezone.utc) == '2026-01-03'
    assert resolve_snooze('1h', NOW, timezone.utc) == NOW + timedelta(hours=1)

def test_consent_and_stale_button(tmp_path):
    log = TodoLog(tmp_path)
    log.add_todo('u', 'task', origin='user', request_id='a', now=NOW, tz=timezone.utc)
    def act(req, **kw):
        return log.apply_action('u', 'T1', 'snooze', by=kw.pop('by', 'agent'), how=kw.pop('how', 'words'), snooze_until=NOW + timedelta(hours=2), request_id=req, now=NOW, tz=timezone.utc, **kw)
    base = dict(user_words='do it', current_message='Please do it', shown_ids=['T1'], turn_key='turn')
    for j, override in enumerate([dict(current_message=None), dict(user_words='old'), dict(current_message='"do it"'), dict(current_message='do it ' + 'x' * 160), dict(shown_ids=[])]):
        assert not act(str(j), **(base | override))['ok']
    assert not act('agent', by='agent', how='command')['ok']
    assert not act('stale', by='user', how='button', rev=0)['ok']
    assert act('ok', **base)['ok']
    assert not act('twice', **base)['ok']

def test_corruption_and_concurrency(tmp_path):
    import threading
    log = TodoLog(tmp_path)
    results = []
    def add(n):
        results.append(log.add_todo('u', 'Task ' + str(n), origin='user', request_id=str(n), now=NOW, tz=timezone.utc))
    threads = [threading.Thread(target=add, args=(n,)) for n in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len({r['id'] for r in results}) == 10
    path = tmp_path / 'asks.jsonl'
    with path.open('ab') as f: f.write(b'not json\n{"op":')
    assert len(log.read().events) == 10
    assert log.read().degraded == 1

def test_ask_promote_close_and_compatibility(tmp_path):
    from plugin.ledger import Ledger
    from plugin.render import render_text, render_html
    ledger = Ledger(tmp_path)
    ledger.add('Question', sender='u', session='s', platform='cli')
    before = ledger.asks()
    text, html = render_text(before, now=NOW), render_html(before, now=NOW)
    log = TodoLog(tmp_path)
    assert render_text(ledger.asks(), now=NOW) == text
    assert render_html(ledger.asks(), now=NOW) == html
    assert log.apply_action('u', 'A1', 'file', by='user', how='command', category='work', request_id='f', now=NOW, tz=timezone.utc)['ok']
    assert len(fold(log.read().events)) == 1
    assert log.apply_action('u', 'A1', 'done', by='user', how='command', request_id='c', now=NOW, tz=timezone.utc)['ok']
    assert ledger.get('A1').reason == 'done'
    assert 'done' in render_text(ledger.asks(), now=NOW, include_closed=True)

def test_suggestion_and_redaction(tmp_path):
    log = TodoLog(tmp_path)
    log.add_todo('u', 'password=secret', origin='agent', turn_key='t', request_id='a', now=NOW, tz=timezone.utc)
    common = dict(by='agent', how='words', request_id='s', now=NOW, tz=timezone.utc)
    assert log.apply_action('u', 'T1', 'suggest', suggestion='done', **common)['ok']
    assert not log.apply_action('u', 'T1', 'suggest', suggestion='done', **(common | {'request_id': 's2'}))['ok']
    assert log.apply_action('u', 'T1', 'answer_suggestion', by='user', how='command', suggestion='declined', request_id='d', now=NOW, tz=timezone.utc)['ok']
    assert not log.apply_action('u', 'T1', 'suggest', suggestion='done', **(common | {'request_id': 's3'}))['ok']
    assert 'secret' not in (tmp_path / 'asks.jsonl').read_text()

def test_single_quoted_words_are_not_consent(tmp_path):
    log = TodoLog(tmp_path)
    log.add_todo('u', 'task', origin='user', request_id='a', now=NOW, tz=timezone.utc)
    result = log.apply_action('u', 'T1', 'done', by='agent', how='words', user_words='done', current_message="She said 'done'", shown_ids=['T1'], turn_key='t', request_id='b', now=NOW, tz=timezone.utc)
    assert not result['ok']

def test_checksum_damage_and_legacy_allocation(tmp_path):
    from plugin.ledger import Ledger
    log = TodoLog(tmp_path)
    log.add_todo('u', 'task', origin='user', request_id='a', now=NOW, tz=timezone.utc)
    path = tmp_path / 'asks.jsonl'
    path.write_text(path.read_text().replace('task', 'tisk'))
    assert log.read().degraded == 1
    ledger = Ledger(tmp_path)
    ledger.add('ask', sender='u', session='s', platform='cli')
    assert ledger.get('A1').text == 'ask'

def test_local_dates_and_dst():
    from zoneinfo import ZoneInfo
    from plugin.todo_model import open_items
    tz = ZoneInfo('Australia/Sydney')
    now = datetime(2026, 10, 3, 15, tzinfo=timezone.utc)
    assert resolve_due('today', now, tz) == '2026-10-04'
    assert resolve_snooze('tomorrow', now, tz) == datetime(2026, 10, 4, 21, tzinfo=timezone.utc)
    items = fold([dict(op='todo_new', id='T1', sender='u', text='tomorrow', origin='user', ts='2026-01-01', due='2026-10-05'), dict(op='todo_new', id='T2', sender='u', text='today', origin='user', ts='2026-01-01', due='2026-10-04')])
    assert [i.id for i in open_items(items, 'u', now, tz)] == ['T2', 'T1']

def test_duplicate_request_remembers_first_result(tmp_path):
    log = TodoLog(tmp_path)
    add = lambda text, req: log.add_todo('u', text, origin='user', request_id=req, now=NOW, tz=timezone.utc)
    add('task', 'a')
    first = add('TASK!', 'b')
    log.apply_action('u', 'T1', 'done', by='user', how='command', request_id='c', now=NOW, tz=timezone.utc)
    assert add('different', 'b') == first

def test_agent_long_text_no_space_and_redaction(tmp_path):
    log = TodoLog(tmp_path)
    result = log.add_todo('u', 'x' * 300, origin='agent', turn_key='t', request_id='password=secret', now=NOW, tz=timezone.utc)
    assert result['shortened']
    assert 'secret' not in (tmp_path / 'asks.jsonl').read_text()

def test_quoted_consent_spanning_quote(tmp_path):
    log = TodoLog(tmp_path)
    log.add_todo('u', 'task', origin='user', request_id='a', now=NOW, tz=timezone.utc)
    result = log.apply_action('u', 'T1', 'done', by='agent', how='words', user_words='please done', current_message='please "old words" done', shown_ids=['T1'], turn_key='t', request_id='b', now=NOW, tz=timezone.utc)
    assert not result['ok']

def test_accept_suggestion_records_answer_and_closure(tmp_path):
    log = TodoLog(tmp_path)
    log.add_todo('u', 'task', origin='user', request_id='a', now=NOW, tz=timezone.utc)
    log.apply_action('u', 'T1', 'suggest', by='agent', how='words', suggestion='done', request_id='s', now=NOW, tz=timezone.utc)
    result = log.apply_action('u', 'T1', 'answer_suggestion', by='user', how='button', rev=2, suggestion='accepted', request_id='b', now=NOW, tz=timezone.utc)
    assert result['state'] == 'done'
    assert [e['op'] for e in log.read().events][-2:] == ['suggestion_answered', 'closed']
    assert result['rev'] == 4

def test_iso_dates_must_be_canonical(tmp_path):
    log = TodoLog(tmp_path)
    assert not log.add_todo('u', 'task', origin='user', due='2026-1-2', request_id='a', now=NOW, tz=timezone.utc)['ok']

def test_quotas_categories_and_length(tmp_path):
    log = TodoLog(tmp_path)
    def add(text, req, **kw):
        return log.add_todo('u', text, request_id=req, now=NOW, tz=timezone.utc, **kw)
    assert not add('x' * 281, 'long', origin='user')['ok']
    assert not add('off', 'off', origin='agent', turn_key='a', agent_adds=False)['ok']
    for n in range(10):
        assert add('agent ' + str(n), 'a' + str(n), origin='agent', turn_key='turn' + str(n // 2))['ok']
    assert not add('extra', 'extra', origin='agent', turn_key='turn0')['ok']
    assert not add('daily', 'daily', origin='agent', turn_key='new')['ok']
    for n in range(12):
        assert add('category ' + str(n), 'c' + str(n), origin='user', category='cat' + str(n))['ok']
    assert not add('overflow', 'overflow', origin='user', category='overflow')['ok']
    assert add('miscellaneous', 'misc', origin='user', category='misc')['ok']

def test_snooze_due_forms_and_counts(tmp_path):
    import pytest
    from plugin.todo_model import action_counts, snoozed_count
    assert resolve_due('today', NOW, timezone.utc) == '2026-01-02'
    assert resolve_due('none', NOW, timezone.utc) == ''
    assert resolve_due('2026-05-02', NOW, timezone.utc) == '2026-05-02'
    assert resolve_snooze('now', NOW, timezone.utc) is None
    assert resolve_snooze('tonight', NOW, timezone.utc).hour == 18
    assert resolve_snooze('tonight', NOW.replace(hour=19), timezone.utc).hour == 20
    assert resolve_snooze('13:00', NOW, timezone.utc).hour == 13
    assert resolve_snooze('2026-01-03', NOW, timezone.utc).hour == 8
    with pytest.raises(ValueError): resolve_snooze('11:00', NOW, timezone.utc)
    log = TodoLog(tmp_path)
    log.add_todo('u', 'task', origin='user', request_id='a', now=NOW, tz=timezone.utc)
    assert log.apply_action('u', 'T1', 'snooze', by='user', how='command', snooze_until=NOW + timedelta(hours=1), request_id='s', now=NOW, tz=timezone.utc)['ok']
    assert snoozed_count(fold(log.read().events), 'u', NOW) == 1
    assert action_counts(log.read().events, 'u', NOW)['last_7_days']['command'] == 1

def test_all_closing_paths_and_closed_refusals(tmp_path):
    for how in ('command', 'button', 'words'):
        for action in ('done', 'drop'):
            log = TodoLog(tmp_path / (how + action))
            log.add_todo('u', 'task', origin='user', request_id='a', now=NOW, tz=timezone.utc)
            kwargs = dict(by='user', how=how, rev=1, user_words='close it', current_message='please close it', shown_ids=['T1'], turn_key='turn')
            result = log.apply_action('u', 'T1', action, request_id='b', now=NOW, tz=timezone.utc, **kwargs)
            assert result['state'] == ('done' if action == 'done' else 'dropped')
            assert log.read().events[-1]['op'] == 'closed'
            assert not log.apply_action('u', 'T1', action, by='user', how='command', request_id='c', now=NOW, tz=timezone.utc)['ok']
            assert not log.apply_action('u', 'T1', 'snooze', by='user', how='command', snooze_until=NOW + timedelta(hours=1), request_id='d', now=NOW, tz=timezone.utc)['ok']

def test_redacted_request_ids_remain_distinct(tmp_path):
    log = TodoLog(tmp_path)
    a = log.add_todo('u', 'first', origin='user', request_id='password=firstsecret', now=NOW, tz=timezone.utc)
    b = log.add_todo('u', 'second', origin='user', request_id='password=secondsecret', now=NOW, tz=timezone.utc)
    assert a['id'] != b['id']

def test_concurrent_duplicates_are_one_item(tmp_path):
    import threading
    log = TodoLog(tmp_path)
    results = []
    def add(n):
        results.append(log.add_todo('u', 'same task!', origin='user', request_id=str(n), now=NOW, tz=timezone.utc))
    threads = [threading.Thread(target=add, args=(n,)) for n in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert len({r['id'] for r in results}) == 1
    assert len(fold(log.read().events)) == 1

def test_category_limit_includes_closed_items(tmp_path):
    log = TodoLog(tmp_path)
    for n in range(12):
        result = log.add_todo('u', 'task ' + str(n), origin='user', category='cat' + str(n), request_id='a' + str(n), now=NOW, tz=timezone.utc)
        assert result['ok']
        assert log.apply_action('u', result['id'], 'done', by='user', how='command', request_id='d' + str(n), now=NOW, tz=timezone.utc)['ok']
    assert not log.add_todo('u', 'extra', origin='user', category='extra', request_id='last', now=NOW, tz=timezone.utc)['ok']

def test_suggestion_expiry_decline_and_stale_accept(tmp_path):
    log = TodoLog(tmp_path)
    log.add_todo('u', 'task', origin='user', request_id='a', now=NOW, tz=timezone.utc)
    def suggest(req, now):
        return log.apply_action('u', 'T1', 'suggest', by='agent', how='words', suggestion='drop', request_id=req, now=now, tz=timezone.utc)
    assert suggest('s1', NOW)['ok']
    assert not log.apply_action('u', 'T1', 'answer_suggestion', by='user', how='button', rev=1, suggestion='accepted', request_id='stale', now=NOW, tz=timezone.utc)['ok']
    assert log.apply_action('u', 'T1', 'answer_suggestion', by='user', how='command', suggestion='declined', request_id='decline', now=NOW, tz=timezone.utc)['ok']
    assert not suggest('s2', NOW + timedelta(hours=23))['ok']
    assert suggest('s3', NOW + timedelta(hours=24))['ok']
    assert suggest('s4', NOW + timedelta(hours=48))['ok']

def test_agent_cannot_set_due_on_user_item(tmp_path):
    log = TodoLog(tmp_path)
    log.add_todo('u', 'task', origin='user', request_id='a', now=NOW, tz=timezone.utc)
    assert not log.apply_action('u', 'T1', 'file', by='agent', how='words', due='2026-01-03', request_id='b', now=NOW, tz=timezone.utc)['ok']
    assert log.apply_action('u', 'T1', 'file', by='agent', how='words', category='work', request_id='c', now=NOW, tz=timezone.utc)['ok']
