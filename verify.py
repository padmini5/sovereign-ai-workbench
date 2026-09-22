from fastapi.testclient import TestClient
from backend.app.main import app
c = TestClient(app)
def login(u,p):
    r = c.post('/api/login', json={'username':u,'password':p}); assert r.status_code==200, r.text
    return r.json()['token']
tf = login('field1','field123'); tm = login('manager1','mgr123')
H=lambda t:{'Authorization':'Bearer '+t}
q={'query':'corroded pipe UT wall loss NDT 14-CR-221','top_k':4}
rf = c.post('/api/query', json=q, headers=H(tf)).json()
rm = c.post('/api/query', json=q, headers=H(tm)).json()
print('FIELD:', [(x['chunk_id'],x['clearance_tier']) for x in rf['chunks']], rf['filter_stats'])
print('MGR:', [(x['chunk_id'],x['clearance_tier']) for x in rm['chunks']], rm['filter_stats'])
assert all(x['clearance_tier']!='confidential' for x in rf['chunks']), 'RBAC leak!'
assert any(x['clearance_tier']=='confidential' for x in rm['chunks']), 'manager should see confidential'
t = c.post('/api/tasks', json={'query':'corroded pipe inspection note'}, headers=H(tm)).json()
print('TASK state:', t['state'], '| events:', len(t['events']))
s = c.post(f"/api/tasks/{t['task_id']}/sign", headers=H(tm)).json()
print('SIGN:', s['status'], s['signature']['algorithm'], s['docx'])
r403 = c.post(f"/api/tasks/{t['task_id']}/sign", headers=H(tf))
print('field sign blocked:', r403.status_code==403)
assert r403.status_code==403
print('monitor:', c.get('/api/monitor', headers=H(tm)).json()['outbound_connections'])
print('ALL CHECKS PASSED')
