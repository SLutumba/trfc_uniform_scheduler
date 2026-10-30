"""TRFC Uniform Hub: department-scoped schedules and attendance."""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone, time as clocktime
from zoneinfo import ZoneInfo
from calendar import monthrange
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, Date, ForeignKey, UniqueConstraint, update, inspect, text, event
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker, Session
from sqlalchemy.exc import IntegrityError

ROOT=Path(__file__).parent
URL=os.getenv('DATABASE_URL', 'sqlite:///'+str(ROOT/'local.db'))
if URL.startswith('postgres://'): URL='postgresql+psycopg://'+URL[len('postgres://'):]
elif URL.startswith('postgresql://'): URL='postgresql+psycopg://'+URL[len('postgresql://'):]
engine=create_engine(URL, pool_pre_ping=True, connect_args={'check_same_thread':False} if URL.startswith('sqlite:') else {})
if engine.dialect.name == 'sqlite':
 @event.listens_for(engine, 'connect')
 def enable_foreign_keys(connection, _):
  cursor=connection.cursor();cursor.execute('PRAGMA foreign_keys=ON');cursor.close()
SessionLocal=sessionmaker(bind=engine, expire_on_commit=False)
class Base(DeclarativeBase): pass
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True); email=Column(String(255),unique=True,nullable=False,index=True)
 password_hash=Column(String(300),nullable=False); role=Column(String(30),nullable=False,default='member'); must_change=Column(Boolean,nullable=False,default=False)
 approved=Column(Boolean,nullable=False,default=True)
 created=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
class Ministry(Base):
 __tablename__='ministries'
 id=Column(Integer,primary_key=True); slug=Column(String(90),unique=True,nullable=False,index=True)
 name=Column(String(100),unique=True,nullable=False); config=Column(Text,nullable=False); version=Column(Integer,default=1,nullable=False)
 published=Column(Text,nullable=True); published_at=Column(DateTime(timezone=True),nullable=True)
class Membership(Base):
 __tablename__='memberships'
 id=Column(Integer,primary_key=True); user_id=Column(Integer,ForeignKey('users.id',ondelete='CASCADE'),nullable=False)
 ministry_id=Column(Integer,ForeignKey('ministries.id',ondelete='CASCADE'),nullable=False)
 __table_args__=(UniqueConstraint('user_id','ministry_id'),)
class AttendanceMeeting(Base):
 __tablename__='attendance_meetings'
 id=Column(Integer,primary_key=True)
 ministry_id=Column(Integer,ForeignKey('ministries.id',ondelete='CASCADE'),nullable=False,index=True)
 service_date=Column(Date,nullable=False,index=True)
 created_by=Column(Integer,ForeignKey('users.id'),nullable=False)
 created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),nullable=False)
 active=Column(Boolean,default=True,nullable=False)
 __table_args__=(UniqueConstraint('ministry_id','service_date',name='one_meeting_per_department_day'),)
class AttendanceRecord(Base):
 __tablename__='attendance_records'
 id=Column(Integer,primary_key=True)
 meeting_id=Column(Integer,ForeignKey('attendance_meetings.id',ondelete='CASCADE'),nullable=False,index=True)
 user_id=Column(Integer,ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
 status=Column(String(20),nullable=False)
 checked_in_at=Column(DateTime(timezone=True),nullable=False)
 __table_args__=(UniqueConstraint('meeting_id','user_id',name='one_checkin_per_member_meeting'),)

class SessionToken(Base):
 __tablename__='sessions'
 id=Column(Integer,primary_key=True); user_id=Column(Integer,ForeignKey('users.id',ondelete='CASCADE'),nullable=False)
 digest=Column(String(64),unique=True,nullable=False,index=True); csrf=Column(String(64),nullable=False)
 expires=Column(DateTime(timezone=True),nullable=False)
class Preference(Base):
 __tablename__='preferences'
 key=Column(String(80),primary_key=True); value=Column(Text,nullable=False)
class Audit(Base):
 __tablename__='audit'
 id=Column(Integer,primary_key=True); actor=Column(String(255),nullable=False)
 verb=Column(String(90),nullable=False); target=Column(String(100),nullable=False)
 created=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
Base.metadata.create_all(engine)
# One-time compatibility migration for databases created by the earlier prototype.
# Schema-altering migrations on a long-lived hosted system should use Alembic.
if inspect(engine).has_table('users') and 'approved' not in {c['name'] for c in inspect(engine).get_columns('users')}:
 with engine.begin() as conn: conn.execute(text('ALTER TABLE users ADD COLUMN approved BOOLEAN NOT NULL DEFAULT TRUE'))
with engine.begin() as conn:
 conn.execute(text("UPDATE users SET role='member' WHERE role='coordinator'"))

app=FastAPI(title='TRFC Uniform Hub',version='1.0.0',docs_url=None,redoc_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
ZA=ZoneInfo('Africa/Johannesburg')
# Always classify on server time. Tests can monkeypatch this one function.
def local_now(): return datetime.now(ZA)
SIGNING_KEY=(os.getenv('ATTENDANCE_SIGNING_KEY') or os.getenv('SETUP_KEY') or secrets.token_urlsafe(32)).encode()
DEFAULT_BRAND={'primary':'#292a2b','accent':'#c49a37','logo':''}
DAYS={'0':'Sunday','5':'Friday'}

def db():
 with SessionLocal() as s: yield s

def audit(s,actor,verb,target): s.add(Audit(actor=actor,verb=verb,target=str(target)))

def hash_password(password):
 if not (12<=len(password)<=128): raise HTTPException(422,'Password must be 12–128 characters.')
 salt=os.urandom(16); data=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1)
 return 'scrypt$'+salt.hex()+'$'+data.hex()

def check_password(password,stored):
 try:
  _,salt_hex,digest=stored.split('$'); computed=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt_hex),n=2**14,r=8,p=1)
  return hmac.compare_digest(computed,bytes.fromhex(digest))
 except (ValueError,TypeError): return False

def now(): return datetime.now(timezone.utc)
def naive_utc(): return datetime.now(timezone.utc).replace(tzinfo=None)
def utc_iso(value):
 """SQLite returns naive UTC; PostgreSQL returns aware timestamps."""
 if value is None:return None
 if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
 return value.astimezone(timezone.utc).isoformat().replace('+00:00','Z')

def secure_equal(value,expected):
 return hmac.compare_digest(value.encode('utf-8'),expected.encode('utf-8'))

def digest(token): return hashlib.sha256(token.encode()).hexdigest()
def require_user(req:Request,s:Session=Depends(db)):
 token=req.cookies.get('trfc_session',''); item=s.query(SessionToken).filter_by(digest=digest(token)).first() if token else None
 expiry=item.expires if item else None
 if expiry is not None and expiry.tzinfo is None:expiry=expiry.replace(tzinfo=timezone.utc)
 if expiry is None or expiry<=now(): raise HTTPException(401,'Please sign in.')
 u=s.get(User,item.user_id)
 if not u: raise HTTPException(401,'Session expired.')
 if req.method not in ('GET','HEAD','OPTIONS'):
  if not secure_equal(req.headers.get('x-csrf-token',''),item.csrf): raise HTTPException(403,'Missing or invalid CSRF token.')
  origin=req.headers.get('origin'); host=req.headers.get('host','')
  if origin and not origin.endswith('://'+host): raise HTTPException(403,'Untrusted origin.')
 return u,item

def require_admin(auth=Depends(require_user)):
 u,_=auth
 if u.role!='admin': raise HTTPException(403,'Administrator access required.')
 return auth

def allowed(s,u,t):
 if not t: raise HTTPException(404,'Ministry not found.')
 if u.role!='admin':
  primary=s.query(Membership).filter_by(user_id=u.id).order_by(Membership.id).first()
  if not primary or primary.ministry_id!=t.id:raise HTTPException(403,'Not assigned to this department.')
 return t

def fields_required(obj,names):
 for name in names:
  if name not in obj: raise ValueError('Missing '+name)

def validate_config(data):
 if not isinstance(data,dict): raise ValueError('Configuration must be an object')
 p=data.get('palette',{}); outfits=data.get('outfits',[]); rules=data.get('rules',[]); settings=data.get('settings',{}); labels=data.get('labels',{})
 if not isinstance(p,dict) or not isinstance(outfits,list) or not isinstance(rules,list) or not isinstance(settings,dict) or not isinstance(labels,dict): raise ValueError('Invalid configuration shape')
 if len(outfits)>1000 or len(rules)>150: raise ValueError('Configuration too large')
 palette={}; allowed_ids={}
 for cat in ('suits','shirts','ties'):
  items=p.get(cat,[])
  if not isinstance(items,list) or len(items)>100: raise ValueError('Invalid '+cat)
  seen=set(); arr=[]
  for x in items:
   if not isinstance(x,dict): raise ValueError('Invalid colour')
   id=str(x.get('id',''))[:100]; label=str(x.get('name','')).strip()[:60]; hexv=x.get('hex','')
   if not id or not label or id in seen or not re.fullmatch(r'#[a-fA-F0-9]{6}',str(hexv)): raise ValueError('Invalid/duplicate '+cat+' colour')
   seen.add(id); arr.append({'id':id,'name':label,'hex':hexv,'confirmed':x.get('confirmed') is not False})
  palette[cat]=arr; allowed_ids[cat]={i['id'] for i in arr}
 outfit_ids=set(); clean_outfits=[]
 for o in outfits:
  if not isinstance(o,dict): raise ValueError('Invalid outfit')
  fields_required(o,['id','suitId','shirtId','tieId']); ident=str(o['id'])[:100]
  if not ident or ident in outfit_ids: raise ValueError('Duplicate outfit ID')
  if any(not isinstance(o[key],str) or o[key] not in allowed_ids[cat] for cat,key in (('suits','suitId'),('shirts','shirtId'),('ties','tieId'))): raise ValueError('Outfit refers to missing colour')
  outfit_ids.add(ident); clean_outfits.append({'id':ident,'suitId':o['suitId'],'shirtId':o['shirtId'],'tieId':o['tieId'],'specialOnly':bool(o.get('specialOnly'))})
 rule_ids=set(); clean_rules=[]
 for r in rules:
  if not isinstance(r,dict): raise ValueError('Invalid rule')
  fields_required(r,['id','kind','outfitId','name'])
  ident=str(r['id'])[:100]; kind=r['kind']; weekday=int(r.get('weekday',0)); dt=str(r.get('date',''))
  if not ident or ident in rule_ids or kind not in ('last','first','second','third','fourth','date') or weekday not in (0,5): raise ValueError('Invalid special rule')
  if not isinstance(r['outfitId'],str) or r['outfitId'] not in outfit_ids: raise ValueError('Rule uses unknown outfit')
  if kind=='date':
   try: d=date.fromisoformat(dt)
   except ValueError: raise ValueError('Invalid rule date')
   if d.weekday() not in (4,6): raise ValueError('Date rule must be Friday/Sunday')
  rule_ids.add(ident); clean_rules.append({'id':ident,'kind':kind,'name':str(r['name']).strip()[:80], 'outfitId':r['outfitId'],'weekday':weekday,'date':dt if kind=='date' else ''})
 start=str(settings.get('startMonth',''))
 if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',start): raise ValueError('Invalid start month')
 months=int(settings.get('months',3))
 if months not in (1,2,3,4,6,12): raise ValueError('Invalid planning period')
 return {'palette':palette,'outfits':clean_outfits,'rules':clean_rules,'settings':{'startMonth':start,'months':months,'includeFridays':bool(settings.get('includeFridays'))},'labels':{k:str(labels.get(k,v))[:40] for k,v in {'suits':'Main garment','shirts':'Second garment','ties':'Accessory'}.items()}}

def generate(config):
 """Server-authoritative deterministic schedule. Dates and weekday use ISO Monday=0."""
 p=config['palette']; garments={cat:{i['id']:i for i in p[cat]} for cat in ('suits','shirts','ties')}
 valid=[o for o in config['outfits'] if all(garments[cat][o[key]]['confirmed'] for cat,key in (('suits','suitId'),('shirts','shirtId'),('ties','tieId')))]
 by_id={o['id']:o for o in valid}
 regular=[o for o in valid if not o['specialOnly']]
 s=config['settings']; yr,mo=map(int,s['startMonth'].split('-'))
 used=[]; result=[]
 for offset in range(s['months']):
  year=yr+(mo-1+offset)//12; month=(mo-1+offset)%12+1
  last=monthrange(year,month)[1]
  for day in range(1,last+1):
   dt=date(year,month,day); wd=dt.weekday()
   if wd!=6 and not (s['includeFridays'] and wd==4): continue
   jsday=0 if wd==6 else 5
   matches=[]
   for index,r in enumerate(config['rules']):
    if r['kind']=='date': ok=r['date']==dt.isoformat()
    else:
     if r['weekday']!=jsday: continue
     occ=(day-1)//7+1
     ok=(r['kind']=='last' and day+7>last) or (r['kind']!='last' and occ=={'first':1,'second':2,'third':3,'fourth':4}.get(r['kind']))
    if ok:matches.append((r['kind']=='date',index,r))
   # Date overrides win; conflicting requirements at the winning level fail.
   selected=[r for is_date,_,r in matches if is_date==any(m[0] for m in matches)]
   if len({r['outfitId'] for r in selected})>1:
    raise HTTPException(422,f'Conflicting mandatory rules on {dt.isoformat()}: '+', '.join(r['name'] for r in selected))
   rule=selected[-1] if selected else None
   if rule and rule['outfitId'] not in by_id:
    raise HTTPException(422,f'Mandatory rule "{rule["name"]}" on {dt.isoformat()} uses an unavailable outfit. Confirm its colours or change the rule.')
   outfit=by_id[rule['outfitId']] if rule else None
   if not outfit and regular:
    counts={o['id']:used.count(o['id']) for o in regular}
    min_count=min(counts.values()); candidates=[o for o in regular if counts[o['id']]<=min_count+1]
    prev=used[-1] if used else None
    candidates.sort(key=lambda o:(o['id']==prev,counts[o['id']],sum(1 for i in used[-4:] if by_id.get(i,{}).get('suitId')==o['suitId']),o['id']))
    outfit=candidates[0]
   if outfit: used.append(outfit['id'])
   result.append({'date':dt.isoformat(),'weekday':jsday,'outfit':{'id':outfit['id'],'parts':{cat:{'name':garments[cat][outfit[key]]['name'],'hex':garments[cat][outfit[key]]['hex']} for cat,key in (('suits','suitId'),('shirts','shirtId'),('ties','tieId'))}} if outfit else None,'rule':rule['name'] if rule else None})
 return result

def snapshot(t):
 return {'id':t.id,'slug':t.slug,'name':t.name,'version':t.version,'config':json.loads(t.config),'published_at':utc_iso(t.published_at)}

def get_brand(s):
 o=s.get(Preference,'branding'); return json.loads(o.value) if o else DEFAULT_BRAND

def policy_response(res):
 res.headers['Cache-Control']='no-store';res.headers['X-Content-Type-Options']='nosniff';res.headers['Referrer-Policy']='strict-origin-when-cross-origin';res.headers['X-Frame-Options']='DENY'
 return res
@app.middleware('http')
async def headers(req,call_next):
 res=await call_next(req)
 if req.url.path.startswith(('/api/','/checkin/','/p/')) or req.url.path=='/':res.headers['Cache-Control']='no-store'
 res.headers['X-Content-Type-Options']='nosniff';res.headers['Referrer-Policy']='strict-origin-when-cross-origin';res.headers['X-Frame-Options']='DENY'
 res.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://prophetdaviduche.net; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
 return res

@app.get('/')
def home():return FileResponse(ROOT/'static'/'index.html')
@app.get('/p/{slug}')
def public_page(slug:str):return RedirectResponse('/?department='+quote(slug),status_code=303)
@app.get('/api/health')
def health(s:Session=Depends(db)):
 s.execute(__import__('sqlalchemy').text('SELECT 1'));return {'status':'ok','database':'connected'}
@app.get('/api/bootstrap-status')
def bootstrap_status(s:Session=Depends(db)):return {'setup_required':s.query(User).count()==0,'ready':bool(os.getenv('SETUP_KEY'))}
@app.get('/api/branding')
def branding(s:Session=Depends(db)):return get_brand(s)

class Credentials(BaseModel): email:str=Field(min_length=3,max_length=255); password:str=Field(min_length=1,max_length=128)
class Setup(Credentials): setup_key:str
@app.post('/api/setup')
def setup(payload:Setup,s:Session=Depends(db)):
 key=os.getenv('SETUP_KEY','')
 if not key:raise HTTPException(503,'SETUP_KEY must be configured by the operator.')
 if not secure_equal(payload.setup_key,key):raise HTTPException(403,'Invalid setup key.')
 if s.query(User).count() or s.get(Preference,'bootstrapped'):raise HTTPException(409,'Administrator already exists.')
 email=payload.email.lower().strip()
 if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise HTTPException(422,'Valid email required.')
 user=User(email=email,password_hash=hash_password(payload.password),role='admin');s.add(user)
 seed=json.loads((ROOT/'protocol_seed.json').read_text(encoding='utf-8'))
 seed['settings']['startMonth']=f'{date.today().year}-{date.today().month:02}'
 seed['labels']={'suits':'Suits','shirts':'Shirts','ties':'Ties'}
 for name,slug in [('Protocol','protocol'),('Choir','choir'),('Ushers','ushers'),('Hospitality','hospitality')]:
  c=seed if slug=='protocol' else {'palette':{'suits':[],'shirts':[],'ties':[]},'outfits':[],'rules':[],'settings':seed['settings'].copy(),'labels':{'suits':'Main garment','shirts':'Second garment','ties':'Accessory'}}
  s.add(Ministry(name=name,slug=slug,config=json.dumps(validate_config(c))))
 s.add(Preference(key='bootstrapped',value='1'));audit(s,email,'setup','workspace')
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'Setup was completed in another session.')
 return {'ok':True,'message':'Administrator and starter ministries created.'}

attempts={}
@app.post('/api/login')
def login(payload:Credentials,req:Request,response:Response,s:Session=Depends(db)):
 ip=req.client.host if req.client else 'unknown';email=payload.email.lower().strip();key=(ip,email); ts=now().timestamp()
 # Remove expired failures and bound failed attempts per client and account.
 for old_key in list(attempts):
  attempts[old_key]=[x for x in attempts[old_key] if ts-x<300]
  if not attempts[old_key]:attempts.pop(old_key,None)
 times=[x for x in attempts.get(key,[]) if ts-x<300]
 if len(times)>=8 or sum(len(v) for k,v in attempts.items() if k[0]==ip)>=40:raise HTTPException(429,'Too many attempts. Try again later.')
 u=s.query(User).filter_by(email=email).first()
 if not u or not check_password(payload.password,u.password_hash):
  attempts[key]=(times+[ts]);raise HTTPException(401,'Invalid email or password.')
 attempts.pop(key,None)
 token=secrets.token_urlsafe(48); csrf=secrets.token_urlsafe(32)
 session=SessionToken(user_id=u.id,digest=digest(token),csrf=csrf,expires=now()+timedelta(days=7));s.add(session);s.commit()
 response.set_cookie('trfc_session',token,httponly=True,secure=os.getenv('APP_ENV')=='production',samesite='lax',max_age=604800,path='/')
 m=s.query(Membership).filter_by(user_id=u.id).order_by(Membership.id).first()
 return {'id':u.id,'email':u.email,'role':u.role,'csrf':csrf,'must_change':u.must_change,'approved':u.approved,'department_id':m.ministry_id if m else None}
@app.post('/api/logout')
def logout(req:Request,res:Response,auth=Depends(require_user),s:Session=Depends(db)):
 _,sess=auth;s.delete(s.merge(sess));s.commit();res.delete_cookie('trfc_session',path='/');return {'ok':True}
@app.get('/api/me')
def me(auth=Depends(require_user),s:Session=Depends(db)):
 u,sess=auth;m=s.query(Membership).filter_by(user_id=u.id).order_by(Membership.id).first()
 return {'id':u.id,'email':u.email,'role':u.role,'csrf':sess.csrf,'must_change':u.must_change,'approved':u.approved,'department_id':m.ministry_id if m else None}

@app.get('/api/teams')
def teams(auth=Depends(require_user),s:Session=Depends(db)):
 u,_=auth
 q=s.query(Ministry).order_by(Ministry.name)
 if u.role!='admin':
  primary=s.query(Membership).filter_by(user_id=u.id).order_by(Membership.id).first()
  q=q.filter(Ministry.id==(primary.ministry_id if primary else -1))
 return [{'id':t.id,'name':t.name,'slug':t.slug,'version':t.version,'published_at':utc_iso(t.published_at)} for t in q.all()]
@app.get('/api/teams/{id}')
def team(id:int,auth=Depends(require_user),s:Session=Depends(db)):
 u,_=auth;t=allowed(s,u,s.get(Ministry,id))
 if u.role!='admin':return {'id':t.id,'name':t.name,'slug':t.slug,'published_at':utc_iso(t.published_at)}
 return snapshot(t)
class NewTeam(BaseModel): name:str=Field(min_length=2,max_length=100)
@app.post('/api/teams')
def new_team(p:NewTeam,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth; name=p.name.strip();slug=re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-')
 if len(slug)<2:raise HTTPException(422,'Name needs at least two Latin letters.')
 template={'palette':{x:[] for x in ('suits','shirts','ties')},'outfits':[],'rules':[],'settings':{'startMonth':f'{date.today().year}-{date.today().month:02}','months':3,'includeFridays':False}}
 t=Ministry(name=name,slug=slug,config=json.dumps(validate_config(template)));s.add(t);audit(s,u.email,'create-ministry',name)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'A ministry with this name or slug already exists.')
 return snapshot(t)
@app.delete('/api/teams/{id}')
def delete_team(id:int,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;t=s.get(Ministry,id)
 if not t:raise HTTPException(404,'Not found.')
 if s.query(Ministry).count()<=1:raise HTTPException(409,'Cannot delete the last ministry.')
 s.query(AttendanceRecord).filter(AttendanceRecord.meeting_id.in_(s.query(AttendanceMeeting.id).filter_by(ministry_id=id))).delete(synchronize_session=False);s.query(AttendanceMeeting).filter_by(ministry_id=id).delete();s.query(Membership).filter_by(ministry_id=id).delete();audit(s,u.email,'delete-ministry',t.name);s.delete(t);s.commit();return {'ok':True}
class SaveConfig(BaseModel): version:int; config:dict
@app.put('/api/teams/{id}/config')
def save_config(id:int,p:SaveConfig,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;t=allowed(s,u,s.get(Ministry,id))
 if t.version!=p.version:raise HTTPException(409,'This ministry was edited elsewhere. Reload before saving to avoid overwriting changes.')
 try:c=validate_config(p.config)
 except (ValueError,TypeError,KeyError) as e:raise HTTPException(422,str(e))
 result=s.execute(update(Ministry).where(Ministry.id==id,Ministry.version==p.version).values(config=json.dumps(c),version=p.version+1))
 if result.rowcount!=1:s.rollback();raise HTTPException(409,'This ministry was edited elsewhere. Reload before saving to avoid overwriting changes.')
 audit(s,u.email,'edit-ministry',t.name);s.commit();return {'version':p.version+1}
@app.get('/api/teams/{id}/preview')
def preview(id:int,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;t=allowed(s,u,s.get(Ministry,id));return {'rows':generate(json.loads(t.config)),'version':t.version}
@app.post('/api/teams/{id}/publish')
def publish(id:int,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;t=allowed(s,u,s.get(Ministry,id));c=json.loads(t.config);schedule=generate(c)
 if not schedule or any(r['outfit'] is None for r in schedule):raise HTTPException(422,'Cannot publish a schedule with unassigned services. Approve regular outfits or rules first.')
 data={'name':t.name,'slug':t.slug,'labels':c['labels'],'settings':c['settings'],'rows':schedule,'published_at':now().isoformat()}
 t.published=json.dumps(data);t.published_at=now();audit(s,u.email,'publish',t.name);s.commit()
 return {'url':'/p/'+quote(t.slug),'published_at':data['published_at'],'count':len(schedule)}
@app.post('/api/teams/{id}/unpublish')
def unpublish(id:int,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;t=allowed(s,u,s.get(Ministry,id));t.published=None;t.published_at=None;audit(s,u.email,'unpublish',t.name);s.commit();return {'ok':True}
@app.get('/api/public/{slug}')
def public_schedule(slug:str,auth=Depends(require_user),s:Session=Depends(db)):
 u,_=auth;t=s.query(Ministry).filter_by(slug=slug).first()
 allowed(s,u,t)
 if not t or not t.published:raise HTTPException(404,'No published schedule for this ministry.')
 return json.loads(t.published)

def csv_safe(value):
 if isinstance(value,str) and (value.startswith(('\t','\r','\n')) or value.lstrip().startswith(('=','+','-','@'))):return "'"+value
 return value

def csv_schedule(obj):
 f=io.StringIO(); w=csv.writer(f);labels=obj.get('labels',{})
 w.writerow(['Date','Day',*(csv_safe(labels.get(k,v)) for k,v in [('suits','Main garment'),('shirts','Second garment'),('ties','Accessory')]),'Special rule'])
 for r in obj['rows']:
  parts=r['outfit']['parts'] if r['outfit'] else {}
  safe=csv_safe
  w.writerow([r['date'],DAYS[str(r['weekday'])],*(safe(parts.get(cat,{}).get('name','')) for cat in ('suits','shirts','ties')),safe(r['rule'] or '')])
 return '\ufeff'+f.getvalue()
@app.get('/api/public/{slug}/csv')
def public_csv(slug:str,auth=Depends(require_user),s:Session=Depends(db)):
 obj=public_schedule(slug,auth,s);return Response(csv_schedule(obj),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{slug}-uniform-schedule.csv"'})
@app.get('/api/teams/{id}/csv')
def private_csv(id:int,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;t=allowed(s,u,s.get(Ministry,id));c=json.loads(t.config)
 return Response(csv_schedule({'labels':c['labels'],'rows':generate(c)}),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="{t.slug}-schedule.csv"'})
@app.get('/api/teams/{id}/backup')
def backup_team(id:int,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;t=allowed(s,u,s.get(Ministry,id));return snapshot(t)
@app.get('/api/users')
def list_users(auth=Depends(require_admin),s:Session=Depends(db)):
 result=[]
 for u in s.query(User).order_by(User.email): result.append({'id':u.id,'email':u.email,'role':u.role,'must_change':u.must_change,'approved':u.approved,'teams':[m.ministry_id for m in s.query(Membership).filter_by(user_id=u.id)]})
 return result
class NewUser(Credentials): role:str='member'; teams:list[int]=[]
@app.post('/api/users')
def new_user(p:NewUser,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;email=p.email.lower().strip()
 if p.role not in ('member','admin'):raise HTTPException(422,'Role must be admin or member.')
 if (p.role=='member' and len(p.teams)!=1) or (p.role=='admin' and p.teams):raise HTTPException(422,'Members require exactly one department; administrators require none.')
 if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise HTTPException(422,'Valid email required.')
 for tid in set(p.teams):
  if not s.get(Ministry,tid):raise HTTPException(422,'Unknown ministry ID')
 user=User(email=email,password_hash=hash_password(p.password),role=p.role,must_change=True,approved=True);s.add(user)
 try:
  s.flush()
  for tid in set(p.teams):s.add(Membership(user_id=user.id,ministry_id=tid))
  audit(s,u.email,'create-'+p.role,email);s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'Account already exists.')
 return {'id':user.id,'email':email}
class Assign(BaseModel): teams:list[int]
@app.put('/api/users/{id}/teams')
def assign_teams(id:int,p:Assign,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;target=s.get(User,id)
 if not target:raise HTTPException(404,'User missing')
 if target.role!='member':raise HTTPException(422,'Administrator has access to all ministries')
 if len(p.teams)!=1:raise HTTPException(422,'Every member must belong to exactly one department.')
 for tid in set(p.teams):
  if not s.get(Ministry,tid):raise HTTPException(422,'Unknown ministry ID')
 s.query(Membership).filter_by(user_id=id).delete()
 for tid in set(p.teams):s.add(Membership(user_id=id,ministry_id=tid))
 audit(s,u.email,'assign-ministries',target.email);s.commit();return {'ok':True}
class PasswordChange(BaseModel): password:str; current_password:str|None=None
@app.put('/api/users/{id}/password')
def change_password(id:int,p:PasswordChange,auth=Depends(require_user),s:Session=Depends(db)):
 u,_=auth;target=s.get(User,id)
 if not target:raise HTTPException(404,'User missing')
 if id!=u.id and u.role!='admin':raise HTTPException(403,'Cannot update other users')
 if id==u.id and not p.current_password:raise HTTPException(422,'Current password required')
 if id==u.id and not check_password(p.current_password,target.password_hash):raise HTTPException(403,'Incorrect current password')
 target.password_hash=hash_password(p.password);target.must_change=(target.id!=u.id)
 s.query(SessionToken).filter(SessionToken.user_id==target.id,SessionToken.id!=auth[1].id if target.id==u.id else SessionToken.id>0).delete(synchronize_session=False)
 audit(s,u.email,'change-password',target.email);s.commit();return {'ok':True}
@app.delete('/api/users/{id}')
def delete_user(id:int,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;target=s.get(User,id)
 if not target:raise HTTPException(404,'User missing')
 if target.id==u.id:raise HTTPException(409,'You cannot delete your own account')
 if target.role=='admin':raise HTTPException(409,'Cannot delete an administrator account')
 s.query(AttendanceRecord).filter_by(user_id=id).delete()
 s.query(Membership).filter_by(user_id=id).delete();s.query(SessionToken).filter_by(user_id=id).delete();audit(s,u.email,'remove-coordinator',target.email);s.delete(target);s.commit();return {'ok':True}
class Appearance(BaseModel): primary:str; accent:str; logo:str
@app.put('/api/branding')
def save_brand(p:Appearance,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth
 if not re.fullmatch(r'#[0-9a-fA-F]{6}',p.primary) or not re.fullmatch(r'#[0-9a-fA-F]{6}',p.accent):raise HTTPException(422,'Invalid brand colours')
 if p.logo and p.logo!='https://prophetdaviduche.net/img/Logo.png' and (not re.fullmatch(r'data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+',p.logo) or len(p.logo)>400000):raise HTTPException(422,'Invalid logo: choose PNG/JPEG/WEBP under 300 KB')
 val=p.model_dump();existing=s.get(Preference,'branding')
 if existing:existing.value=json.dumps(val)
 else:s.add(Preference(key='branding',value=json.dumps(val)))
 audit(s,u.email,'update-branding','workspace');s.commit();return val


# ---- Member registration and department-locked read model ----
@app.get('/api/departments')
def departments(s:Session=Depends(db)):
 return [{'id':t.id,'name':t.name} for t in s.query(Ministry).order_by(Ministry.name)]

class Registration(Credentials): department_id:int
@app.post('/api/register',status_code=201)
def register(p:Registration,s:Session=Depends(db)):
 email=p.email.lower().strip()
 if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise HTTPException(422,'Valid email required.')
 if not s.get(Ministry,p.department_id):raise HTTPException(422,'Unknown department.')
 # Registration creates a member, never an admin. Admin must approve attendance eligibility.
 user=User(email=email,password_hash=hash_password(p.password),role='member',approved=False)
 s.add(user)
 try:
  s.flush();s.add(Membership(user_id=user.id,ministry_id=p.department_id));s.commit()
 except IntegrityError:
  s.rollback();raise HTTPException(409,'This email is already registered.')
 return {'ok':True,'message':'Account created. An administrator must approve attendance eligibility.'}

@app.get('/api/my-schedule')
def my_schedule(auth=Depends(require_user),s:Session=Depends(db)):
 u,_=auth
 if u.role=='admin':raise HTTPException(422,'Select a department as an administrator.')
 m=s.query(Membership).filter_by(user_id=u.id).order_by(Membership.id).first()
 if not m:raise HTTPException(403,'No department assigned. Contact an administrator.')
 t=allowed(s,u,s.get(Ministry,m.ministry_id))
 if not t.published:return {'name':t.name,'slug':t.slug,'published':False,'rows':[],'labels':{}}
 return {**json.loads(t.published),'published':True}

@app.put('/api/users/{id}/approval')
def approve_member(id:int,p:dict,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;target=s.get(User,id)
 if not target or target.role!='member':raise HTTPException(404,'Member not found.')
 if type(p.get('approved')) is not bool:raise HTTPException(422,'An approved boolean is required.')
 target.approved=p['approved'];audit(s,u.email,'approve-member' if target.approved else 'suspend-member',target.email);s.commit()
 return {'id':target.id,'approved':target.approved}

# ---- Attendance QR codes ----
def attendance_sig(meeting:AttendanceMeeting)->str:
 msg=f'{meeting.id}:{meeting.ministry_id}:{meeting.service_date.isoformat()}'.encode()
 return hmac.new(SIGNING_KEY,msg,hashlib.sha256).hexdigest()

def meeting_url(req:Request,meeting:AttendanceMeeting):
 # The QR carries the meeting's opaque signed locator. The server verifies login and department.
 return str(req.base_url).rstrip('/')+f'/checkin/{meeting.id}/{attendance_sig(meeting)}'

def require_meeting(s,meeting_id:int):
 meeting=s.get(AttendanceMeeting,meeting_id)
 if not meeting:raise HTTPException(404,'Attendance meeting not found.')
 return meeting

def attendance_status(ts:datetime)->str:
 local=ts.astimezone(ZA).time()
 if local < clocktime(8,30):return 'on_time'
 if local < clocktime(15,0):return 'late'
 return 'absent'

class MeetingRequest(BaseModel): service_date:date|None=None
@app.post('/api/teams/{id}/attendance',status_code=201)
def create_meeting(id:int,p:MeetingRequest,req:Request,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;t=allowed(s,u,s.get(Ministry,id));when=p.service_date or local_now().date()
 if when<local_now().date():raise HTTPException(422,'Cannot open historical attendance meetings.')
 meeting=s.query(AttendanceMeeting).filter_by(ministry_id=id,service_date=when).first()
 if not meeting:
  meeting=AttendanceMeeting(ministry_id=id,service_date=when,created_by=u.id,active=True);s.add(meeting)
  try:s.flush()
  except IntegrityError:s.rollback();raise HTTPException(409,'Meeting already exists; reload the list.')
  audit(s,u.email,'open-attendance',f'{t.slug}/{when}');s.commit()
 elif not meeting.active:raise HTTPException(409,'This attendance session has been closed.')
 return {'id':meeting.id,'department':t.name,'date':when.isoformat(),'active':meeting.active,'qr_url':meeting_url(req,meeting),'qr_image':f'/api/attendance/{meeting.id}/qr.png'}

@app.get('/api/teams/{id}/attendance')
def list_meetings(id:int,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;t=allowed(s,u,s.get(Ministry,id))
 meetings=s.query(AttendanceMeeting).filter_by(ministry_id=t.id).order_by(AttendanceMeeting.service_date.desc()).limit(80).all()
 return [{'id':m.id,'date':m.service_date.isoformat(),'active':m.active,'checkins':s.query(AttendanceRecord).filter_by(meeting_id=m.id).count()} for m in meetings]

@app.get('/api/attendance/{id}')
def attendance_report(id:int,req:Request,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;m=require_meeting(s,id);t=allowed(s,u,s.get(Ministry,m.ministry_id))
 members=s.query(User).join(Membership,Membership.user_id==User.id).filter(Membership.ministry_id==t.id,User.role=='member').order_by(User.email).all()
 entries={r.user_id:r for r in s.query(AttendanceRecord).filter_by(meeting_id=id)}
 past=m.service_date<local_now().date() or (m.service_date==local_now().date() and local_now().time()>=clocktime(15,0))
 roster=[{'user_id':member.id,'email':member.email,'approved':member.approved,'status':entries[member.id].status if member.id in entries else ('absent' if past else 'pending'),'checked_in_at':utc_iso(entries[member.id].checked_in_at) if member.id in entries else None} for member in members]
 return {'id':m.id,'name':t.name,'date':m.service_date.isoformat(),'active':m.active,'timezone':'Africa/Johannesburg','qr_url':meeting_url(req,m) if m.active else None,'qr_image':f'/api/attendance/{m.id}/qr.png' if m.active else None,'roster':roster}

@app.get('/api/attendance/{id}/csv')
def attendance_csv(id:int,req:Request,auth=Depends(require_admin),s:Session=Depends(db)):
 report=attendance_report(id,req,auth,s)
 out=io.StringIO();writer=csv.writer(out);writer.writerow(['Department','Meeting date','Member email','Approved','Attendance','Checked in (UTC)'])
 for r in report['roster']:
  email=r['email']
  if email.startswith(('=','+','-','@')):email="'"+email
  writer.writerow([csv_safe(report['name']),report['date'],csv_safe(email),'Yes' if r['approved'] else 'No',r['status'],r['checked_in_at'] or ''])
 return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="attendance-{id}.csv"'})

@app.get('/api/attendance/{id}/qr.png')
def qr_image(id:int,req:Request,auth=Depends(require_admin),s:Session=Depends(db)):
 import qrcode
 m=require_meeting(s,id)
 if not m.active:raise HTTPException(410,'Attendance session is closed.')
 u,_=auth;allowed(s,u,s.get(Ministry,m.ministry_id))
 image=qrcode.make(meeting_url(req,m));out=io.BytesIO();image.save(out,format='PNG');out.seek(0)
 return StreamingResponse(out,media_type='image/png',headers={'Cache-Control':'no-store'})

@app.post('/api/attendance/{id}/close')
def close_meeting(id:int,auth=Depends(require_admin),s:Session=Depends(db)):
 u,_=auth;m=require_meeting(s,id);allowed(s,u,s.get(Ministry,m.ministry_id));m.active=False
 audit(s,u.email,'close-attendance',id);s.commit();return {'ok':True}

@app.get('/checkin/{meeting_id}/{signature}')
def checkin_page(meeting_id:int,signature:str):
 return FileResponse(ROOT/'static'/'checkin.html')

class CheckinRequest(BaseModel): signature:str
@app.post('/api/attendance/{id}/check-in')
def check_in(id:int,p:CheckinRequest,auth=Depends(require_user),s:Session=Depends(db)):
 u,_=auth;m=require_meeting(s,id)
 if not m.active:raise HTTPException(410,'Attendance is closed.')
 if not secure_equal(p.signature,attendance_sig(m)):raise HTTPException(403,'Invalid attendance QR code.')
 allowed(s,u,s.get(Ministry,m.ministry_id))
 if u.role!='member':raise HTTPException(403,'Attendance is only for department members.')
 if not u.approved:raise HTTPException(403,'Your membership is awaiting administrator approval.')
 current=local_now()
 if current.date()!=m.service_date:raise HTTPException(403,'This QR code is only valid on its meeting date (Africa/Johannesburg).')
 record=s.query(AttendanceRecord).filter_by(meeting_id=id,user_id=u.id).first()
 if record:return {'status':record.status,'already_checked_in':True,'checked_in_at':utc_iso(record.checked_in_at)}
 status=attendance_status(current)
 record=AttendanceRecord(meeting_id=id,user_id=u.id,status=status,checked_in_at=current.astimezone(timezone.utc));s.add(record)
 try:s.commit()
 except IntegrityError:
  s.rollback();record=s.query(AttendanceRecord).filter_by(meeting_id=id,user_id=u.id).first()
  if not record:raise HTTPException(409,'Concurrent check-in; try again.')
  return {'status':record.status,'already_checked_in':True,'checked_in_at':utc_iso(record.checked_in_at)}
 return {'status':status,'already_checked_in':False,'checked_in_at':utc_iso(record.checked_in_at)}

@app.get('/api/me/attendance')
def my_attendance(auth=Depends(require_user),s:Session=Depends(db)):
 u,_=auth
 entries=s.query(AttendanceRecord,AttendanceMeeting).join(AttendanceMeeting,AttendanceMeeting.id==AttendanceRecord.meeting_id).filter(AttendanceRecord.user_id==u.id).order_by(AttendanceMeeting.service_date.desc()).limit(30).all()
 return [{'date':m.service_date.isoformat(),'status':r.status,'checked_in_at':utc_iso(r.checked_in_at)} for r,m in entries]
