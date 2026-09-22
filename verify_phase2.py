from fastapi.testclient import TestClient
from backend.app.main import app
c = TestClient(app)
def login(u,p):
    r = c.post('/api/login', json={'username':u,'password':p}); assert r.status_code==200, r.text
    return r.json()['token']
tf = login('field1','field123'); tm = login('manager1','mgr123')
H=lambda t:{'Authorization':'Bearer '+t}
# 1. RAG backend + RBAC preserved
q={'query':'corroded pipe UT wall loss NDT 14-CR-221','top_k':4}
rf = c.post('/api/query', json=q, headers=H(tf)).json()
rm = c.post('/api/query', json=q, headers=H(tm)).json()
print('FIELD backend:', rf['filter_stats'], [x['chunk_id'] for x in rf['chunks']])
print('MGR backend:', rm['filter_stats'], [(x['chunk_id'],x['clearance_tier']) for x in rm['chunks']])
assert all(x['clearance_tier']!='confidential' for x in rf['chunks']), 'RBAC leak!'
assert any(x['clearance_tier']=='confidential' for x in rm['chunks'])
assert 'backend' in rf['filter_stats']
# 2. languages endpoint: 21 entries (en + 20)
langs = c.get('/api/languages').json()
print('languages:', len(langs['languages']), 'default:', langs['default'])
assert len(langs['languages']) == 21 and langs['default'] == 'en'
# 3. health: honest pqc + llm mode + rag backend
h = c.get('/api/health').json()
print('health:', {k: h.get(k) for k in ('llm_mode','rag_backend')}, h['pqc_status'])
assert h['pqc_status']['pqc_available'] is False
assert 'NOT ML-DSA' in h['pqc'] or 'ML-DSA' in h['pqc']
assert h['llm_mode'] in ('ollama','local-fallback')
assert 'rag_backend' in h
# 4. task flow + 403 preserved + pqc honesty in task response
t = c.post('/api/tasks', json={'query':'corroded pipe inspection note'}, headers=H(tm)).json()
assert 'llm_mode' in t and 'pqc' in t
s = c.post(f"/api/tasks/{t['task_id']}/sign", headers=H(tm)).json()
print('SIGN:', s['status'], '|', s['signature']['algorithm'], '| pqc_available:', s['signature']['pqc_available'])
assert s['signature']['pqc_available'] is False
assert 'NOT ML-DSA' in s['signature']['algorithm']
r403 = c.post(f"/api/tasks/{t['task_id']}/sign", headers=H(tf))
assert r403.status_code==403, 'field sign must be 403'
print('field sign blocked: True')
mon = c.get('/api/monitor', headers=H(tm)).json()
assert mon['app_egress_connections'] == 0
print('app egress:', mon['app_egress_connections'])
print('EXTENDED CHECKS PASSED')
