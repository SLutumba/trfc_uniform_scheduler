import importlib
import json
import os
import sys
from datetime import date
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient

@pytest.fixture
def env(tmp_path,monkeypatch):
 monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'test.db'))
 monkeypatch.setenv('SETUP_KEY','very-long-secret-setup-key-for-testing')
 if 'app' in sys.modules:del sys.modules['app']
 mod=importlib.import_module('app')
 client=TestClient(mod.app)
 assert client.get('/api/health').json()['status']=='ok'
 return mod,client

def setup(client):
 r=client.post('/api/setup',json={'email':'admin@trfc.test','password':'secure-password-123','setup_key':'very-long-secret-setup-key-for-testing'});assert r.status_code==200,r.text
 r=client.post('/api/login',json={'email':'admin@trfc.test','password':'secure-password-123'});assert r.status_code==200,r.text
 return r.json()['csrf']

def test_setup_auth_security(env):
 mod,c=env
 assert c.get('/api/teams').status_code==401
 assert c.get('/api/bootstrap-status').json()['setup_required']
 assert c.post('/api/setup',json={'email':'a@b.co','password':'valid-password-123','setup_key':'bad'}).status_code==403
 csrf=setup(c)
 assert c.post('/api/setup',json={'email':'a@b.co','password':'valid-password-123','setup_key':'very-long-secret-setup-key-for-testing'}).status_code==409
 assert c.post('/api/teams',json={'name':'Media'}).status_code==403
 assert c.post('/api/teams',json={'name':'Media'},headers={'x-csrf-token':csrf}).status_code==200
 r=c.get('/api/teams'); assert len(r.json())==5
 assert c.get('/api/users').status_code==200
 assert 'HttpOnly' in c.post('/api/login',json={'email':'admin@trfc.test','password':'secure-password-123'}).headers['set-cookie']

def test_ministry_edit_publish_snapshot(env):
 mod,c=env; csrf=setup(c);hdr={'x-csrf-token':csrf}
 teams=c.get('/api/teams').json(); protocol=next(t for t in teams if t['slug']=='protocol'); choir=next(t for t in teams if t['slug']=='choir')
 config=c.get(f'/api/teams/{protocol["id"]}').json(); assert len(config['config']['outfits'])==25
 assert c.get('/api/public/protocol').status_code==404
 out=c.post(f'/api/teams/{protocol["id"]}/publish',headers=hdr); assert out.status_code==200,out.text
 public=c.get('/api/public/protocol').json(); assert public['rows']; assert public['rows'][0]['outfit']
 old=public['rows'][0]['outfit']['parts']['suits']['name']
 cfg=config['config'];cfg['palette']['suits'][0]['name']='Ebony'
 r=c.put(f'/api/teams/{protocol["id"]}/config',json={'version':config['version'],'config':cfg},headers=hdr);assert r.status_code==200,r.text
 assert c.get('/api/public/protocol').json()['rows'][0]['outfit']['parts']['suits']['name']==old
 assert c.put(f'/api/teams/{protocol["id"]}/config',json={'version':config['version'],'config':cfg},headers=hdr).status_code==409
 assert c.post(f'/api/teams/{choir["id"]}/publish',headers=hdr).status_code==422
 assert c.get('/api/public/choir').status_code==404
 assert c.get('/api/public/protocol/csv').status_code==200
 assert c.post(f'/api/teams/{protocol["id"]}/unpublish',headers=hdr).status_code==200
 assert c.get('/api/public/protocol').status_code==404

def test_staff_permissions_and_password(env):
 mod,c=env; csrf=setup(c);h={'x-csrf-token':csrf};tt=c.get('/api/teams').json()
 p=next(t for t in tt if t['slug']=='protocol')['id']; ch=next(t for t in tt if t['slug']=='choir')['id']
 r=c.post('/api/users',json={'email':'singer@trfc.test','password':'temporarypass123','teams':[ch]},headers=h); assert r.status_code==200,r.text
 uid=r.json()['id'];staff=TestClient(mod.app)
 r=staff.post('/api/login',json={'email':'singer@trfc.test','password':'temporarypass123'});assert r.status_code==200
 sh={'x-csrf-token':r.json()['csrf']}
 assert [t['slug'] for t in staff.get('/api/teams').json()]==['choir']
 assert staff.get(f'/api/teams/{p}').status_code==403
 assert staff.get(f'/api/teams/{ch}').status_code==200
 assert staff.post('/api/teams',json={'name':'Finance'},headers=sh).status_code==403
 assert staff.get('/api/users').status_code==403
 assert staff.put(f'/api/users/{uid}/password',json={'password':'changedlongpass456'},headers=sh).status_code==422
 assert staff.put(f'/api/users/{uid}/password',json={'password':'changedlongpass456','current_password':'temporarypass123'},headers=sh).status_code==200
 assert c.put(f'/api/users/{uid}/teams',json={'teams':[p,ch]},headers=h).status_code==422
 assert c.put(f'/api/users/{uid}/teams',json={'teams':[p]},headers=h).status_code==200
 assert staff.get(f'/api/teams/{p}').status_code==200
 assert staff.get(f'/api/teams/{ch}').status_code==403
 assert c.delete(f'/api/users/{uid}',headers=h).status_code==200
 assert staff.get('/api/me').status_code==401

def test_schedule_rules_fridays_and_override(env):
 mod,c=env;setup(c);team=next(x for x in c.get('/api/teams').json() if x['slug']=='protocol')
 cfg=c.get(f'/api/teams/{team["id"]}').json()['config']
 cfg['settings']={'startMonth':'2026-11','months':3,'includeFridays':True}
 rows=mod.generate(mod.validate_config(cfg))
 sundays=[r for r in rows if r['weekday']==0]; fridays=[r for r in rows if r['weekday']==5]
 assert sundays and fridays
 for month,last_sunday in [('2026-11','2026-11-29'),('2026-12','2026-12-27'),('2027-01','2027-01-31')]:
  special=next(r for r in rows if r['date']==last_sunday)
  assert special['rule']=='All-black month end'
  assert all(special['outfit']['parts'][k]['name']=='Black' for k in ('suits','shirts','ties'))
 cfg['rules'].append({'id':'override','kind':'date','date':'2026-11-29','weekday':0,'name':'Conference','outfitId':'c-2'})
 updated=mod.generate(mod.validate_config(cfg))
 assert next(r for r in updated if r['date']=='2026-11-29')['rule']=='Conference'
 assert len(updated)>len(sundays)

def test_reject_invalid_data_and_csv_formula(env):
 mod,c=env;csrf=setup(c);h={'x-csrf-token':csrf};t=next(t for t in c.get('/api/teams').json() if t['slug']=='protocol')
 conf=c.get('/api/teams/'+str(t['id'])).json()
 cfg=conf['config'];cfg['outfits'][0]['suitId']='missing'
 assert c.put('/api/teams/'+str(t['id'])+'/config',json={'version':conf['version'],'config':cfg},headers=h).status_code==422
 f=mod.csv_schedule({'labels':{},'rows':[{'date':'2026-11-01','weekday':0,'outfit':{'parts':{'suits':{'name':'=HYPERLINK(1)'},'shirts':{'name':'White'},'ties':{'name':'Black'}}},'rule':None}]})
 assert "'=HYPERLINK(1)" in f


def test_branding_admin_only_and_public_pages(env):
 mod,c=env;csrf=setup(c)
 assert c.get('/').status_code==200
 assert c.get('/p/protocol').status_code==200
 assert c.get('/static/style.css').status_code==200
 assert c.get('/api/branding').json()['logo']==''
 good={'primary':'#222222','accent':'#ccaa44','logo':'https://prophetdaviduche.net/img/Logo.png'}
 assert c.put('/api/branding',json=good).status_code==403
 assert c.put('/api/branding',json=good,headers={'x-csrf-token':csrf}).status_code==200
 assert c.get('/api/branding').json()['accent']=='#ccaa44'
 assert c.put('/api/branding',json={**good,'logo':'javascript:alert(1)'},headers={'x-csrf-token':csrf}).status_code==422
 assert c.put('/api/branding',json={**good,'primary':'red'},headers={'x-csrf-token':csrf}).status_code==422


def test_coordinator_revocation_and_team_deletion(env):
 mod,c=env;csrf=setup(c);h={'x-csrf-token':csrf}
 all_teams=c.get('/api/teams').json();p=next(t for t in all_teams if t['slug']=='protocol')['id']
 new=c.post('/api/teams',headers=h,json={'name':'Media Production'});assert new.status_code==200
 media=new.json()['id'];assert c.get('/api/teams/'+str(media)).json()['config']['outfits']==[]
 user=c.post('/api/users',headers=h,json={'email':'person@trfc.test','password':'temporarypass123','teams':[media]}).json()
 staff=TestClient(mod.app);login_response=staff.post('/api/login',json={'email':'person@trfc.test','password':'temporarypass123'})
 assert login_response.status_code==200
 assert staff.get('/api/teams/'+str(media)).status_code==200
 assert c.delete('/api/teams/'+str(media),headers=h).status_code==200
 assert staff.get('/api/teams/'+str(media)).status_code==404
 assert staff.get('/api/teams').json()==[]
 assert c.delete('/api/users/'+str(user['id']),headers=h).status_code==200
 assert staff.get('/api/me').status_code==401
 assert c.get('/api/teams/'+str(p)).status_code==200


def test_registration_department_lock_admin_only_edits(env):
 mod,c=env;admin_csrf=setup(c);hdr={'x-csrf-token':admin_csrf}
 teams=c.get('/api/departments').json();protocol=next(t['id'] for t in teams if t['name']=='Protocol');choir=next(t['id'] for t in teams if t['name']=='Choir')
 assert c.post('/api/register',json={'email':'member@trfc.test','password':'member-password-123','department_id':protocol}).status_code==201
 assert c.post('/api/register',json={'email':'member@trfc.test','password':'member-password-123','department_id':choir}).status_code==409
 member=TestClient(mod.app);resp=member.post('/api/login',json={'email':'member@trfc.test','password':'member-password-123'});assert resp.status_code==200
 mcsrf={'x-csrf-token':resp.json()['csrf']};assert resp.json()['role']=='member';assert not resp.json()['approved']
 assert [t['slug'] for t in member.get('/api/teams').json()]==['protocol']
 assert member.get('/api/teams/'+str(choir)).status_code==403
 assert 'config' not in member.get('/api/teams/'+str(protocol)).json()
 config=c.get('/api/teams/'+str(protocol)).json()
 assert member.get('/api/teams/'+str(protocol)+'/backup').status_code==403
 assert member.get('/api/teams/'+str(protocol)+'/preview').status_code==403
 assert member.get('/api/teams/'+str(protocol)+'/csv').status_code==403
 assert member.put('/api/teams/'+str(protocol)+'/config',headers=mcsrf,json={'version':config['version'],'config':config['config']}).status_code==403
 assert member.post('/api/teams/'+str(protocol)+'/publish',headers=mcsrf).status_code==403
 assert member.post('/api/teams/'+str(protocol)+'/unpublish',headers=mcsrf).status_code==403
 assert member.get('/api/public/protocol').status_code==404
 assert member.get('/api/public/choir').status_code==403
 assert TestClient(mod.app).get('/api/public/protocol').status_code==401
 assert c.post('/api/teams/'+str(protocol)+'/publish',headers=hdr).status_code==200
 assert member.get('/api/my-schedule').json()['name']=='Protocol'
 assert member.get('/api/public/protocol').status_code==200
 assert member.get('/api/public/choir').status_code==403
 assert member.get('/api/public/choir/csv').status_code==403
 assert member.get('/api/my-schedule').json()['rows']
 assert member.put('/api/users/'+str(resp.json()['id'])+'/teams',headers=mcsrf,json={'teams':[choir]}).status_code==403


def test_attendance_qr_time_boundaries_approval_and_duplicates(env,monkeypatch):
 from datetime import datetime
 from zoneinfo import ZoneInfo
 mod,c=env;csrf=setup(c);h={'x-csrf-token':csrf};zone=ZoneInfo('Africa/Johannesburg')
 monkeypatch.setattr(mod,'local_now',lambda:datetime(2026,11,1,8,29,59,tzinfo=zone))
 protocol=next(t['id'] for t in c.get('/api/departments').json() if t['name']=='Protocol')
 choir=next(t['id'] for t in c.get('/api/departments').json() if t['name']=='Choir')
 reg=lambda email,dep:c.post('/api/register',json={'email':email,'password':'test-password-456','department_id':dep})
 for email,dep in [('one@trfc.test',protocol),('two@trfc.test',protocol),('three@trfc.test',protocol),('four@trfc.test',protocol),('other@trfc.test',choir)]:assert reg(email,dep).status_code==201
 members=c.get('/api/users').json()
 for u in members:
  if u['role']=='member':assert c.put(f'/api/users/{u["id"]}/approval',headers=h,json={'approved':True}).status_code==200
 r=c.post(f'/api/teams/{protocol}/attendance',headers=h,json={'service_date':'2026-11-01'})
 assert r.status_code==201,r.text;meeting=r.json();mid=meeting['id'];sig=meeting['qr_url'].split('/')[-1]
 assert c.get(f'/api/attendance/{mid}/qr.png').status_code==200
 assert c.get(f'/api/attendance/{mid}/qr.png').headers['content-type']=='image/png'
 assert TestClient(mod.app).get(f'/api/attendance/{mid}/qr.png').status_code==401
 assert c.get(f'/api/attendance/{mid}').status_code==200
 assert c.post(f'/api/teams/{protocol}/attendance',headers=h,json={'service_date':'2026-11-01'}).json()['id']==mid
 def member_login(email):
  client=TestClient(mod.app);resp=client.post('/api/login',json={'email':email,'password':'test-password-456'});assert resp.status_code==200
  return client,{'x-csrf-token':resp.json()['csrf']}
 client,header=member_login('one@trfc.test')
 assert client.post(f'/api/attendance/{mid}/check-in',headers=header,json={'signature':'bad'}).status_code==403
 assert client.get(f'/api/attendance/{mid}').status_code==403
 assert client.post(f'/api/attendance/{mid}/check-in',headers=header,json={'signature':sig}).json()['status']=='on_time'
 monkeypatch.setattr(mod,'local_now',lambda:datetime(2026,11,1,8,30,0,tzinfo=zone))
 assert client.post(f'/api/attendance/{mid}/check-in',headers=header,json={'signature':sig}).json()=={'status':'on_time','already_checked_in':True,'checked_in_at':'2026-11-01T06:29:59Z'}
 late_client,late_h=member_login('two@trfc.test')
 assert late_client.post(f'/api/attendance/{mid}/check-in',headers=late_h,json={'signature':sig}).json()['status']=='late'
 monkeypatch.setattr(mod,'local_now',lambda:datetime(2026,11,1,15,0,0,tzinfo=zone))
 absent_client,absent_h=member_login('three@trfc.test')
 assert absent_client.post(f'/api/attendance/{mid}/check-in',headers=absent_h,json={'signature':sig}).json()['status']=='absent'
 report=c.get(f'/api/attendance/{mid}').json()
 assert {x['email']:x['status'] for x in report['roster']}=={'one@trfc.test':'on_time','two@trfc.test':'late','three@trfc.test':'absent','four@trfc.test':'absent'}
 wrong,wh=member_login('other@trfc.test')
 assert wrong.post(f'/api/attendance/{mid}/check-in',headers=wh,json={'signature':sig}).status_code==403
 assert c.post(f'/api/attendance/{mid}/check-in',headers=h,json={'signature':sig}).status_code==403
 assert c.post(f'/api/attendance/{mid}/close',headers=h).status_code==200
 assert client.post(f'/api/attendance/{mid}/check-in',headers=header,json={'signature':sig}).status_code==410
 assert c.get(f'/api/attendance/{mid}/qr.png').status_code==410


def test_unapproved_member_and_wrong_day_qr(env,monkeypatch):
 from datetime import datetime
 from zoneinfo import ZoneInfo
 mod,c=env;csrf=setup(c);header={'x-csrf-token':csrf};zone=ZoneInfo('Africa/Johannesburg')
 monkeypatch.setattr(mod,'local_now',lambda:datetime(2026,11,1,7,0,tzinfo=zone))
 protocol=next(t['id'] for t in c.get('/api/departments').json() if t['name']=='Protocol')
 assert c.post('/api/register',json={'email':'unapproved@trfc.test','password':'test-password-456','department_id':protocol}).status_code==201
 meeting=c.post(f'/api/teams/{protocol}/attendance',headers=header,json={'service_date':'2026-11-01'}).json()
 person=TestClient(mod.app);r=person.post('/api/login',json={'email':'unapproved@trfc.test','password':'test-password-456'});mh={'x-csrf-token':r.json()['csrf']}
 assert person.post(f'/api/attendance/{meeting["id"]}/check-in',headers=mh,json={'signature':meeting['qr_url'].split('/')[-1]}).status_code==403
 u=next(u for u in c.get('/api/users').json() if u['email']=='unapproved@trfc.test')
 assert c.put('/api/users/'+str(u['id'])+'/approval',headers=header,json={'approved':True}).status_code==200
 monkeypatch.setattr(mod,'local_now',lambda:datetime(2026,11,2,7,0,tzinfo=zone))
 assert person.post(f'/api/attendance/{meeting["id"]}/check-in',headers=mh,json={'signature':meeting['qr_url'].split('/')[-1]}).status_code==403
 assert c.post(f'/api/teams/{protocol}/attendance',headers=header,json={'service_date':'2026-10-29'}).status_code==422


def test_additional_admin_and_rollcall_csv(env,monkeypatch):
 from datetime import datetime
 from zoneinfo import ZoneInfo
 mod,c=env;h={'x-csrf-token':setup(c)}
 r=c.post('/api/users',headers=h,json={'email':'second-admin@trfc.test','password':'new-admin-password-123','role':'admin','teams':[]})
 assert r.status_code==200,r.text
 second=TestClient(mod.app);login=second.post('/api/login',json={'email':'second-admin@trfc.test','password':'new-admin-password-123'})
 assert login.status_code==200 and login.json()['role']=='admin'
 second_headers={'x-csrf-token':login.json()['csrf']}
 protocol=next(t for t in second.get('/api/teams').json() if t['slug']=='protocol')
 assert second.post('/api/teams/'+str(protocol['id'])+'/publish',headers=second_headers).status_code==200
 monkeypatch.setattr(mod,'local_now',lambda:datetime(2026,11,1,15,1,tzinfo=ZoneInfo('Africa/Johannesburg')))
 meeting=c.post('/api/teams/'+str(protocol['id'])+'/attendance',headers=h,json={'service_date':'2026-11-01'}).json()
 r=second.get('/api/attendance/'+str(meeting['id'])+'/csv')
 assert r.status_code==200 and r.headers['content-type'].startswith('text/csv')
 assert 'Attendance' in r.text
