"""Subscription, usage, and Stripe webhook primitives for Apex AI."""
from __future__ import annotations
import hashlib,hmac,json,secrets,sqlite3,time
from dataclasses import dataclass
PLANS={"free":{"price":0,"questions":100,"documents":5,"premium":False},"pro":{"price":20,"questions":5000,"documents":100,"premium":True},"business":{"price":99,"questions":50000,"documents":1000,"premium":True}}
class BillingError(RuntimeError): pass
@dataclass(frozen=True)
class Plan: name:str; price:int; questions:int; documents:int; premium:bool
class BillingService:
 def __init__(self,path="billing.sqlite3"):
  self.path=str(path)
  with self.db() as d:d.executescript("CREATE TABLE IF NOT EXISTS accounts(user_id TEXT PRIMARY KEY,plan TEXT NOT NULL DEFAULT 'free',stripe_customer TEXT,subscription TEXT); CREATE TABLE IF NOT EXISTS usage(user_id TEXT,period TEXT,kind TEXT,count INTEGER,PRIMARY KEY(user_id,period,kind));")
 def db(self):
  d=sqlite3.connect(self.path);d.row_factory=sqlite3.Row;return d
 def plan(self,name):
  if name not in PLANS:raise BillingError("Unknown plan.")
  return Plan(name,**PLANS[name])
 def get_plan(self,user_id):
  with self.db() as d:r=d.execute("SELECT plan FROM accounts WHERE user_id=?",(user_id,)).fetchone()
  return self.plan(r["plan"] if r else "free")
 def change_plan(self,user_id,name,customer=None,subscription=None):
  self.plan(name)
  with self.db() as d:d.execute("INSERT INTO accounts(user_id,plan,stripe_customer,subscription) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan,stripe_customer=COALESCE(excluded.stripe_customer,accounts.stripe_customer),subscription=COALESCE(excluded.subscription,accounts.subscription)",(user_id,name,customer,subscription))
 def record_usage(self,user_id,kind,amount=1,period=None):
  period=period or time.strftime('%Y-%m')
  with self.db() as d:d.execute("INSERT INTO usage VALUES(?,?,?,?) ON CONFLICT(user_id,period,kind) DO UPDATE SET count=count+excluded.count",(user_id,period,kind,amount))
 def usage(self,user_id,kind,period=None):
  period=period or time.strftime('%Y-%m')
  with self.db() as d:r=d.execute("SELECT count FROM usage WHERE user_id=? AND period=? AND kind=?",(user_id,period,kind)).fetchone()
  return r["count"] if r else 0
 def allowed(self,user_id,kind):
  p=self.get_plan(user_id); limit=p.questions if kind=="questions" else p.documents
  return self.usage(user_id,kind)<limit
 def require_premium(self,user_id):
  if not self.get_plan(user_id).premium:raise BillingError("This feature requires a Pro or Business plan.")
 def billing_summary(self,user_id):
  p=self.get_plan(user_id);return {"plan":p.name,"price":p.price,"questions_used":self.usage(user_id,"questions"),"questions_limit":p.questions,"documents_used":self.usage(user_id,"documents"),"documents_limit":p.documents,"premium":p.premium}

def verify_stripe_signature(payload:bytes,signature:str,secret:str,tolerance=300):
 try:parts=dict(item.split('=',1) for item in signature.split(','));timestamp=int(parts['t']);provided=parts['v1']
 except (ValueError,KeyError):return False
 if abs(time.time()-timestamp)>tolerance:return False
 expected=hmac.new(secret.encode(),f"{timestamp}.{payload.decode()}".encode(),hashlib.sha256).hexdigest()
 return hmac.compare_digest(expected,provided)

def handle_webhook(payload:bytes,signature:str,secret:str,service:BillingService):
 if not verify_stripe_signature(payload,signature,secret):raise BillingError("Invalid webhook signature.")
 event=json.loads(payload);data=event.get('data',{}).get('object',{});user=data.get('metadata',{}).get('user_id')
 if user and event.get('type') in {'customer.subscription.created','customer.subscription.updated'}:service.change_plan(user,data.get('metadata',{}).get('plan','free'),data.get('customer'),data.get('id'))
 elif user and event.get('type')=='customer.subscription.deleted':service.change_plan(user,'free')
 return event.get('type')
