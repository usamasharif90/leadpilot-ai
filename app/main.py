
from __future__ import annotations
from fastapi import FastAPI, Request, Form, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, or_
from sqlalchemy.exc import IntegrityError
from pathlib import Path
from datetime import datetime
import os, json, random, math, csv, io, subprocess, sys
import requests
import stripe

from .models import *
from .auth import hash_password, verify_password, make_session, read_session

BASE=Path(__file__).resolve().parent.parent

def _load_local_env(path:Path):
    """Load simple KEY=VALUE entries. Last duplicate wins; exported nonblank values win."""
    if not path.exists():
        return
    parsed={}
    for raw in path.read_text().splitlines():
        line=raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key,value=line.split("=",1)
        key=key.strip(); value=value.strip()
        if len(value)>=2 and value[0]==value[-1] and value[0] in ("'", '"'):
            value=value[1:-1]
        if key:
            parsed[key]=value
    for key,value in parsed.items():
        if not os.environ.get(key):
            os.environ[key]=value

_load_local_env(BASE/".env")

templates=Jinja2Templates(directory=BASE/"app"/"templates")
app=FastAPI(title="LeadPilot AI", version="1.0")
app.mount("/static", StaticFiles(directory=BASE/"app"/"static"), name="static")

PLANS={
 "Starter":{"limit":100,"price":199},
 "Growth":{"limit":300,"price":399},
 "Scale":{"limit":750,"price":699},
}

ACTIVE_SUBSCRIPTION_STATUSES={"active","trialing","trial"}

def stripe_price_ids():
    return {
        "Starter":os.getenv("STRIPE_PRICE_STARTER","").strip(),
        "Growth":os.getenv("STRIPE_PRICE_GROWTH","").strip(),
        "Scale":os.getenv("STRIPE_PRICE_SCALE","").strip(),
    }

def plan_from_price_id(price_id:str):
    if not price_id:
        return None
    for name,pid in stripe_price_ids().items():
        if pid and pid==price_id:
            return name
    return None

def _stripe_obj_get(obj, key, default=None):
    try:
        if isinstance(obj,dict):
            return obj.get(key,default)
        return getattr(obj,key,default)
    except Exception:
        return default

def _subscription_price_id(subscription):
    items=_stripe_obj_get(subscription,"items")
    data=_stripe_obj_get(items,"data",[]) or []
    if not data:
        return ""
    price=_stripe_obj_get(data[0],"price")
    return _stripe_obj_get(price,"id","") or ""

def _subscription_period_end(subscription):
    value=_stripe_obj_get(subscription,"current_period_end")
    if not value:
        return None
    try:
        return datetime.utcfromtimestamp(int(value))
    except Exception:
        return None

def apply_stripe_subscription(db, workspace, subscription):
    status=_stripe_obj_get(subscription,"status","inactive") or "inactive"
    price_id=_subscription_price_id(subscription)
    plan=plan_from_price_id(price_id)
    customer_id=_stripe_obj_get(subscription,"customer","") or ""
    sub_id=_stripe_obj_get(subscription,"id","") or ""

    if customer_id:
        workspace.stripe_customer_id=str(customer_id)
    if sub_id:
        workspace.stripe_subscription_id=str(sub_id)
    if price_id:
        workspace.stripe_price_id=str(price_id)

    workspace.subscription_status=str(status)
    workspace.stripe_current_period_end=_subscription_period_end(subscription)
    workspace.stripe_cancel_at_period_end=bool(_stripe_obj_get(subscription,"cancel_at_period_end",False))

    if plan:
        workspace.plan=plan
        workspace.monthly_limit=PLANS[plan]["limit"]
    elif status not in ACTIVE_SUBSCRIPTION_STATUSES:
        workspace.monthly_limit=0

def workspace_for_stripe_object(db, obj):
    metadata=_stripe_obj_get(obj,"metadata",{}) or {}
    wid=_stripe_obj_get(metadata,"workspace_id")
    if wid:
        try:
            w=db.get(Workspace,int(wid))
            if w:
                return w
        except Exception:
            pass
    customer=_stripe_obj_get(obj,"customer","") or ""
    if customer:
        return db.scalar(select(Workspace).where(Workspace.stripe_customer_id==str(customer)))
    return None

def app_url():
    return os.getenv("APP_URL","http://127.0.0.1:8000").rstrip("/")

def get_db():
    return SessionLocal()

def current_user(request:Request):
    uid=read_session(request.cookies.get("lp_session"))
    if not uid: return None
    with get_db() as db:
        return db.get(User,uid)

def workspace_for_user(user_id:int):
    with get_db() as db:
        m=db.scalar(select(Membership).where(Membership.user_id==user_id))
        return db.get(Workspace,m.workspace_id) if m else None

def require(request:Request):
    u=current_user(request)
    if not u: raise HTTPException(401)
    w=workspace_for_user(u.id)
    return u,w

def ctx(request, active, **extra):
    u=current_user(request); w=workspace_for_user(u.id) if u else None
    d={"request":request,"active":active,"user":u,"workspace":w,"plans":PLANS}
    d.update(extra); return d

def activity(db, wid, label, detail=""):
    db.add(Activity(workspace_id=wid,label=label,detail=detail))

def link_job_lead(db, job_id:int, lead_id:int):
    if not db.scalar(select(JobLead.id).where(JobLead.job_id==job_id, JobLead.lead_id==lead_id)):
        db.add(JobLead(job_id=job_id, lead_id=lead_id))

def completed_discovery_jobs(db, wid:int, limit:int=50):
    return db.scalars(
        select(Job).where(
            Job.workspace_id==wid,
            Job.job_type=="discovery",
            Job.status=="completed"
        ).order_by(Job.id.desc()).limit(limit)
    ).all()


def seed_workspace(db,wid):
    if db.scalar(select(func.count()).select_from(Lead).where(Lead.workspace_id==wid)): return
    random.seed(wid*31)
    first=["Sarah","James","Alex","Jordan","Taylor","Morgan","Casey","Riley","Avery","Jamie","Drew","Cameron","Parker","Reese","Logan","Blake"]
    last=["Mitchell","Carter","Brooks","Reed","Foster","Hayes","Bennett","Price","Turner","Coleman","Sullivan","Perry","Morgan","Bailey","Ward","Murphy"]
    niches=["Business Coaching","Fitness Coaching","Career Coaching","Relationship Coaching","Mindset Coaching","Marketing Coaching","Sales Coaching","Real Estate Coaching"]
    for i in range(110):
        fn,ln=random.choice(first),random.choice(last); niche=random.choice(niches); region=random.choice(["USA","UK","AU"]); platform=random.choice(["YouTube","Instagram"])
        followers=int(10**random.uniform(math.log10(5000),math.log10(600000))); views=int(followers*random.uniform(.8,9))
        score=min(99,max(45,int(60+(views>100000)*12+random.randint(-10,15))))
        db.add(Lead(workspace_id=wid,platform=platform,external_id=f"demo-{wid}-{i}",name=f"{fn} {ln}",handle=f"@{fn.lower()}{ln.lower()}{i}",
                    profile_url="https://example.com",niche=niche,followers=followers,views_30d=views,region=region,language="English",
                    sells_coaching=True,coaching_confidence=round(random.uniform(.76,.98),2),faceless_screen="likely_personal",
                    email=f"{fn.lower()}.{ln.lower()}@example.com" if random.random()<.7 else "",email_confidence=.88,ig_handle=f"@{fn.lower()}{ln.lower()}",
                    ig_followers=int(followers*.6),score=score,status=random.choice(["New","Qualified","Saved","Contacted"]),
                    evidence="Coaching language detected, active recent content, target geography and personality-led profile.",source="demo"))
    for n,d in [("Hot Leads","Score 80+ prospects"),("Needs Enrichment","Missing contact data"),("Client Shortlist","Ready for review")]:
        db.add(LeadList(workspace_id=wid,name=n,description=d))
    activity(db,wid,"Workspace created","Demo data seeded for product tour")

def ensure_demo():
    if os.getenv("DEMO_SEED","true").lower()!="true": return
    with get_db() as db:
        if db.scalar(select(func.count()).select_from(User))==0:
            u=User(email="demo@leadpilot.local",password_hash=hash_password("demo1234"),full_name="Demo Owner",is_admin=True)
            db.add(u);db.flush()
            w=Workspace(name="Demo Agency",owner_user_id=u.id,plan="Growth",subscription_status="trial",monthly_limit=300)
            db.add(w);db.flush()
            db.add(Membership(workspace_id=w.id,user_id=u.id,role="owner"))
            seed_workspace(db,w.id);db.commit()

@app.on_event("startup")
def startup():
    init_db(); ensure_demo()

@app.exception_handler(401)
def unauthorized(request, exc):
    return RedirectResponse("/login",303)

@app.get("/login",response_class=HTMLResponse)
def login_page(request:Request):
    if current_user(request): return RedirectResponse("/",303)
    return templates.TemplateResponse("login.html",{"request":request,"mode":"login"})

@app.post("/login")
def login(request:Request,email:str=Form(...),password:str=Form(...)):
    with get_db() as db:
        u=db.scalar(select(User).where(User.email==email.lower().strip()))
        if not u or not verify_password(password,u.password_hash):
            return templates.TemplateResponse("login.html",{"request":request,"mode":"login","error":"Invalid email or password"})
        r=RedirectResponse("/",303);r.set_cookie("lp_session",make_session(u.id),httponly=True,samesite="lax",secure=False,max_age=60*60*24*30);return r

@app.get("/signup",response_class=HTMLResponse)
def signup_page(request:Request):
    if os.getenv("ALLOW_SIGNUP","true").lower()!="true": return RedirectResponse("/login",303)
    return templates.TemplateResponse("login.html",{"request":request,"mode":"signup"})

@app.post("/signup")
def signup(full_name:str=Form(...),workspace_name:str=Form(...),email:str=Form(...),password:str=Form(...)):
    if len(password)<8: return RedirectResponse("/signup?error=password",303)
    with get_db() as db:
        try:
            u=User(email=email.lower().strip(),password_hash=hash_password(password),full_name=full_name);db.add(u);db.flush()
            trial_leads=max(0,int(os.getenv("FREE_TRIAL_LEADS","0") or 0))
            trial_status="trial" if trial_leads>0 else "inactive"
            w=Workspace(name=workspace_name,owner_user_id=u.id,plan="Starter",subscription_status=trial_status,monthly_limit=trial_leads);db.add(w);db.flush()
            db.add(Membership(workspace_id=w.id,user_id=u.id,role="owner"));db.commit()
            r=RedirectResponse("/billing?welcome=1",303);r.set_cookie("lp_session",make_session(u.id),httponly=True,samesite="lax",max_age=60*60*24*30);return r
        except IntegrityError:
            db.rollback();return RedirectResponse("/signup?error=email",303)

@app.get("/logout")
def logout():
    r=RedirectResponse("/login",303);r.delete_cookie("lp_session");return r

@app.get("/",response_class=HTMLResponse)
def dashboard(request:Request):
    u,w=require(request)
    if w.subscription_status not in ACTIVE_SUBSCRIPTION_STATUSES or w.monthly_limit<=0:
        return RedirectResponse("/billing?subscription_required=1",303)
    with get_db() as db:
        leads=db.scalars(select(Lead).where(Lead.workspace_id==w.id).order_by(Lead.score.desc()).limit(8)).all()
        stats={
          "leads":db.scalar(select(func.count()).select_from(Lead).where(Lead.workspace_id==w.id)),
          "hot":db.scalar(select(func.count()).select_from(Lead).where(Lead.workspace_id==w.id,Lead.score>=80)),
          "emails":db.scalar(select(func.count()).select_from(Lead).where(Lead.workspace_id==w.id,Lead.email!="")),
          "clients":db.scalar(select(func.count()).select_from(Client).where(Client.workspace_id==w.id)),
        }
        acts=db.scalars(select(Activity).where(Activity.workspace_id==w.id).order_by(Activity.id.desc()).limit(6)).all()
    return templates.TemplateResponse("dashboard.html",ctx(request,"dashboard",stats=stats,leads=leads,activities=acts))

@app.get("/finder",response_class=HTMLResponse)
def finder(request:Request,platform:str="All",niche:str="All",region:str="All",keyword:str="",min_followers:int=5000,max_followers:int=500000,min_views:int=20000,min_score:int=60,run_id:int=0):
    u,w=require(request)
    with get_db() as db:
        runs=completed_discovery_jobs(db,w.id)
        selected_run_id=run_id
        if selected_run_id==0 and runs:
            selected_run_id=runs[0].id

        q=select(Lead).where(
            Lead.workspace_id==w.id,
            Lead.followers>=min_followers,
            Lead.followers<=max_followers,
            or_(Lead.platform!="YouTube",Lead.views_30d>=min_views),
            Lead.score>=min_score,
            Lead.sells_coaching==True,
            Lead.faceless_screen!="likely_faceless"
        )
        if selected_run_id>0:
            q=q.join(JobLead, JobLead.lead_id==Lead.id).where(JobLead.job_id==selected_run_id)
        if platform!="All": q=q.where(Lead.platform==platform)
        if niche!="All": q=q.where(Lead.niche==niche)
        if region!="All": q=q.where(Lead.region==region)
        if keyword:
            q=q.where(or_(
                Lead.name.ilike(f"%{keyword}%"),
                Lead.handle.ilike(f"%{keyword}%"),
                Lead.niche.ilike(f"%{keyword}%")
            ))
        rows=db.scalars(q.order_by(Lead.score.desc()).limit(250)).all()
        niches=[x for (x,) in db.execute(
            select(Lead.niche).where(Lead.workspace_id==w.id).distinct().order_by(Lead.niche)
        )]
    filters={
        "platform": platform,
        "niche": niche,
        "region": region,
        "keyword": keyword,
        "min_followers": min_followers,
        "max_followers": max_followers,
        "min_views": min_views,
        "min_score": min_score,
        "run_id": selected_run_id,
    }
    return templates.TemplateResponse(
        "finder.html",
        ctx(request,"finder",leads=rows,niches=niches,filters=filters,runs=runs,selected_run_id=selected_run_id)
    )

@app.post("/saved-searches")
def save_search(request:Request,name:str=Form(...),filters_json:str=Form(...)):
    u,w=require(request)
    with get_db() as db:
        db.add(SavedSearch(workspace_id=w.id,name=name,filters_json=filters_json));activity(db,w.id,"Search saved",name);db.commit()
    return RedirectResponse("/finder",303)

@app.get("/leads",response_class=HTMLResponse)
def leads_page(request:Request,status:str="All",platform:str="All"):
    u,w=require(request)
    with get_db() as db:
        q=select(Lead).where(Lead.workspace_id==w.id)
        if status!="All":
            q=q.where(Lead.status==status)
        if platform!="All":
            q=q.where(Lead.platform==platform)
        rows=db.scalars(q.order_by(Lead.score.desc()).limit(500)).all()
    return templates.TemplateResponse(
        "leads.html",
        ctx(request,"leads",leads=rows,status=status,platform=platform)
    )

@app.post("/leads/{lead_id}/status")
def lead_status(request:Request,lead_id:int,status:str=Form(...)):
    u,w=require(request)
    with get_db() as db:
        l=db.get(Lead,lead_id)
        if not l or l.workspace_id!=w.id: raise HTTPException(404)
        l.status=status;activity(db,w.id,"Lead status updated",f"{l.name} → {status}");db.commit()
    return RedirectResponse("/leads",303)

@app.get("/api/lead/{lead_id}")
def api_lead(request:Request,lead_id:int):
    u,w=require(request)
    with get_db() as db:
        l=db.get(Lead,lead_id)
        if not l or l.workspace_id!=w.id: raise HTTPException(404)
        return {c.name:getattr(l,c.name) for c in l.__table__.columns}

@app.get("/lists",response_class=HTMLResponse)
def lists_page(request:Request):
    u,w=require(request)
    with get_db() as db:
        lists=db.scalars(select(LeadList).where(LeadList.workspace_id==w.id).order_by(LeadList.id.desc())).all()
        counts={li.id:db.scalar(select(func.count()).select_from(LeadListItem).where(LeadListItem.list_id==li.id)) for li in lists}
    return templates.TemplateResponse("lists.html",ctx(request,"lists",lists=lists,counts=counts))

@app.post("/lists")
def create_list(request:Request,name:str=Form(...),description:str=Form("")):
    u,w=require(request)
    with get_db() as db:
        db.add(LeadList(workspace_id=w.id,name=name,description=description));activity(db,w.id,"List created",name);db.commit()
    return RedirectResponse("/lists",303)

@app.post("/lists/{list_id}/add/{lead_id}")
def add_to_list(request:Request,list_id:int,lead_id:int):
    u,w=require(request)
    with get_db() as db:
        li=db.get(LeadList,list_id);lead=db.get(Lead,lead_id)
        if not li or li.workspace_id!=w.id or not lead or lead.workspace_id!=w.id: raise HTTPException(404)
        try: db.add(LeadListItem(list_id=list_id,lead_id=lead_id));db.commit()
        except IntegrityError: db.rollback()
    return JSONResponse({"ok":True})

@app.get("/clients",response_class=HTMLResponse)
def clients_page(request:Request):
    u,w=require(request)
    with get_db() as db: rows=db.scalars(select(Client).where(Client.workspace_id==w.id).order_by(Client.id.desc())).all()
    return templates.TemplateResponse("clients.html",ctx(request,"clients",clients=rows))

@app.post("/clients")
def add_client(request:Request,name:str=Form(...),niche:str=Form(...),region:str=Form(...),plan:str=Form(...)):
    u,w=require(request);price={"Pilot":500,"Growth":950,"Scale":1500}.get(plan,500)
    with get_db() as db:
        db.add(Client(workspace_id=w.id,name=name,niche=niche,region=region,plan=plan,status="Active",monthly_value=price));activity(db,w.id,"Client added",name);db.commit()
    return RedirectResponse("/clients",303)

@app.get("/batches",response_class=HTMLResponse)
def batches_page(request:Request):
    u,w=require(request)
    with get_db() as db:
        batches=db.scalars(select(Batch).where(Batch.workspace_id==w.id).order_by(Batch.id.desc())).all()
        clients={c.id:c.name for c in db.scalars(select(Client).where(Client.workspace_id==w.id))}
    return templates.TemplateResponse("batches.html",ctx(request,"batches",batches=batches,clients=clients))

@app.post("/batches")
def create_batch(request:Request,name:str=Form(...),client_id:int=Form(...),lead_count:int=Form(100)):
    u,w=require(request)
    with get_db() as db:
        db.add(Batch(workspace_id=w.id,client_id=client_id,name=name,lead_count=lead_count,status="Preparing"));activity(db,w.id,"Batch created",name);db.commit()
    return RedirectResponse("/batches",303)

@app.get("/outreach",response_class=HTMLResponse)
def outreach_page(request:Request):
    u,w=require(request)
    with get_db() as db:
        leads=db.scalars(select(Lead).where(Lead.workspace_id==w.id,Lead.score>=80).order_by(Lead.score.desc()).limit(50)).all()
        drafts=db.scalars(select(OutreachDraft).where(OutreachDraft.workspace_id==w.id).order_by(OutreachDraft.id.desc()).limit(20)).all()
    return templates.TemplateResponse("outreach.html",ctx(request,"outreach",leads=leads,drafts=drafts))

@app.post("/outreach/{lead_id}")
def generate_outreach(request:Request,lead_id:int,channel:str=Form("email")):
    u,w=require(request)
    with get_db() as db:
        l=db.get(Lead,lead_id)
        if not l or l.workspace_id!=w.id: raise HTTPException(404)
        first=l.name.split()[0] if l.name else "there"
        msg=(f"Hey {first} — came across your {l.platform} content in {l.niche.lower()}. "
             f"You’ve built strong recent momentum with about {l.views_30d:,} views over the last 30 days. "
             "We help creator-led coaching businesses turn that attention into more qualified sales calls. "
             "Open to me sending over 2–3 ideas I’d test for your brand?")
        db.add(OutreachDraft(workspace_id=w.id,lead_id=l.id,channel=channel,message=msg));activity(db,w.id,"Outreach drafted",l.name);db.commit()
    return RedirectResponse("/outreach",303)


def apify_instagram_search(keywords:str, limit:int=25):
    """Search public Instagram profiles through Apify's Instagram Search Scraper."""
    token=os.getenv("APIFY_API_TOKEN","").strip()
    if not token:
        return []
    url=f"https://api.apify.com/v2/actors/apify~instagram-search-scraper/run-sync-get-dataset-items?token={token}"
    payload={"search":keywords,"searchType":"user","searchLimit":limit}
    r=requests.post(url,json=payload,timeout=180)
    r.raise_for_status()
    data=r.json()
    return data if isinstance(data,list) else []

def _to_int(value):
    try:
        return int(float(value or 0))
    except Exception:
        return 0

def _coaching_signal(text:str, keywords:str):
    t=(text or "").lower()
    needles=["coach","coaching","mentor","consultant","consulting","mastermind","program","course","academy","trainer"]
    kws=[x.strip().lower() for x in (keywords or "").replace(","," ").split() if len(x.strip())>2]
    hits=sum(1 for n in needles if n in t)
    kw_hits=sum(1 for k in kws if k in t)
    conf=min(0.98,0.55 + hits*0.08 + kw_hits*0.04)
    return (hits>0 or kw_hits>=2), round(conf,2)

def import_instagram_leads(wid:int,payload:dict,job_id:int):
    token=os.getenv("APIFY_API_TOKEN","").strip()
    if not token:
        return 0
    rows=apify_instagram_search(payload.get("keywords","business coach"), limit=30)
    min_followers=int(payload.get("min_followers",5000))
    max_followers=int(payload.get("max_followers",500000))
    with get_db() as db:
        n=0
        for r in rows:
            username=(r.get("username") or "").strip()
            ext=str(r.get("id") or username).strip()
            if not ext or not username:
                continue
            followers=_to_int(r.get("followersCount") or r.get("followers") or r.get("followedByCount"))
            # Search results sometimes omit follower count. Keep the live result if count is unknown;
            # otherwise enforce the requested audience range.
            if followers and not (min_followers <= followers <= max_followers):
                continue
            bio=r.get("biography") or r.get("bio") or ""
            full_name=r.get("fullName") or r.get("name") or username
            profile_url=r.get("url") or f"https://www.instagram.com/{username}/"
            sells,conf=_coaching_signal(f"{full_name} {bio}",payload.get("keywords",""))
            if not sells:
                continue
            exists=db.scalar(select(Lead).where(Lead.workspace_id==wid,Lead.platform=="Instagram",Lead.external_id==ext))
            if exists:
                # refresh mutable live fields
                exists.name=full_name
                exists.handle="@"+username.lstrip("@")
                exists.profile_url=profile_url
                exists.followers=followers or exists.followers
                exists.ig_handle="@"+username.lstrip("@")
                exists.ig_followers=followers or exists.ig_followers
                exists.evidence=bio[:1000]
                exists.coaching_confidence=conf
                exists.updated_at=datetime.utcnow()
                link_job_lead(db,job_id,exists.id)
                continue
            score=70
            if followers>=10000: score+=5
            if followers>=50000: score+=5
            if conf>=0.8: score+=10
            score=min(score,95)
            lead=Lead(
                workspace_id=wid,platform="Instagram",external_id=ext,name=full_name,
                handle="@"+username.lstrip("@"),profile_url=profile_url,
                niche=payload.get("niche","Unclassified"),followers=followers,views_30d=0,
                region="",language="English",sells_coaching=True,coaching_confidence=conf,
                faceless_screen="uncertain",email="",ig_handle="@"+username.lstrip("@"),
                ig_followers=followers,score=score,status="New",evidence=bio[:1000],source="apify_instagram_live"
            )
            db.add(lead); db.flush()
            link_job_lead(db,job_id,lead.id)
            n+=1
        db.commit()
        return n


# ---------- Live enrichment helpers ----------
_SOCIAL_DOMAINS={
    "youtube.com","youtu.be","instagram.com","facebook.com","twitter.com","x.com",
    "tiktok.com","linkedin.com","linktr.ee","beacons.ai","calendly.com","cal.com",
    "patreon.com","discord.gg","discord.com","amazon.com","amzn.to","bit.ly","tinyurl.com"
}
_DOMAIN_RE=__import__("re").compile(r"https?://(?:www\\.)?([A-Za-z0-9.-]+\\.[A-Za-z]{2,})", __import__("re").IGNORECASE)

def _extract_domain(text:str):
    for m in _DOMAIN_RE.finditer(text or ""):
        d=m.group(1).lower().strip(".")
        if d not in _SOCIAL_DOMAINS and not any(d.endswith("."+x) for x in _SOCIAL_DOMAINS):
            return d
    return ""

def _ig_urls(row:dict):
    vals=[]
    for key in ("externalUrl","website","websiteUrl","bioLink","urlExternal"):
        v=row.get(key)
        if isinstance(v,str) and v.startswith("http"):
            vals.append(v)
    for key in ("externalUrls","bioLinks","links"):
        v=row.get(key)
        if isinstance(v,list):
            for item in v:
                if isinstance(item,str) and item.startswith("http"):
                    vals.append(item)
                elif isinstance(item,dict):
                    for k in ("url","href","lynx_url","link"):
                        u=item.get(k)
                        if isinstance(u,str) and u.startswith("http"):
                            vals.append(u)
    # preserve order / de-dupe
    return list(dict.fromkeys(vals))

def _norm(s:str):
    import re
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def _best_ig_match(lead, rows):
    """Conservative name/handle match so we don't attach a random Instagram account."""
    from difflib import SequenceMatcher
    target=_norm(lead.handle.lstrip("@") if lead.handle else lead.name)
    name_target=_norm(lead.name)
    best=None; best_score=0.0
    for r in rows or []:
        username=(r.get("username") or "").lstrip("@")
        fullname=r.get("fullName") or r.get("name") or ""
        u=_norm(username); n=_norm(fullname)
        scores=[SequenceMatcher(None,target,u).ratio() if target and u else 0,
                SequenceMatcher(None,name_target,n).ratio() if name_target and n else 0,
                SequenceMatcher(None,name_target,u).ratio() if name_target and u else 0]
        score=max(scores)
        if target and u and (target in u or u in target): score=max(score,0.88)
        if name_target and n and (name_target in n or n in name_target): score=max(score,0.88)
        if score>best_score:
            best_score=score; best=r
    return best if best_score>=0.58 else None

def hunter_domain_lookup(domain:str):
    key=os.getenv("HUNTER_API_KEY","").strip()
    if not key or not domain:
        return "",0.0
    try:
        r=requests.get("https://api.hunter.io/v2/domain-search",params={"domain":domain,"api_key":key},timeout=30)
        if r.status_code==401:
            raise RuntimeError("Hunter rejected HUNTER_API_KEY (401)")
        r.raise_for_status()
        emails=((r.json().get("data") or {}).get("emails") or [])
        if not emails:
            return "",0.0
        # Prefer a high-confidence generic/personal result rather than blindly taking an arbitrary row.
        emails=sorted(emails,key=lambda x: float(x.get("confidence") or 0),reverse=True)
        e=emails[0]
        return (e.get("value") or ""), float(e.get("confidence") or 0)/100.0
    except Exception as e:
        print(f"Hunter lookup failed for {domain}: {e}")
        return "",0.0

def _public_email_from_ig(row:dict):
    for key in ("businessEmail","publicEmail","email","contactEmail"):
        value=row.get(key)
        if isinstance(value,str) and "@" in value:
            return value.strip()
    return ""

def enrich_workspace_leads(wid:int, max_leads:int=20):
    """Match live IG profiles and add public/Hunter email data to live leads."""
    has_apify=bool(os.getenv("APIFY_API_TOKEN","").strip())
    has_hunter=bool(os.getenv("HUNTER_API_KEY","").strip())
    if not has_apify and not has_hunter:
        return {"instagram":0,"email":0,"checked":0}

    with get_db() as db:
        leads=db.scalars(
            select(Lead).where(
                Lead.workspace_id==wid,
                Lead.source!="demo",
                or_(Lead.ig_handle=="", Lead.email=="")
            ).order_by(Lead.score.desc()).limit(max_leads)
        ).all()
        ig_count=0; email_count=0; checked=0
        for lead in leads:
            checked+=1
            candidate=None

            if has_apify:
                try:
                    query=(lead.ig_handle or lead.handle or lead.name or "").lstrip("@").strip()
                    if query:
                        rows=apify_instagram_search(query,limit=5)
                        if lead.platform=="Instagram":
                            wanted=(lead.ig_handle or lead.handle or "").lstrip("@").lower()
                            candidate=next((r for r in rows if (r.get("username") or "").lower()==wanted),None) or _best_ig_match(lead,rows)
                        else:
                            candidate=_best_ig_match(lead,rows)
                except Exception as e:
                    print(f"Instagram match failed for {lead.name}: {e}")

            if candidate:
                username=(candidate.get("username") or "").lstrip("@").strip()
                followers=_to_int(candidate.get("followersCount") or candidate.get("followers") or candidate.get("followedByCount"))
                if username and not lead.ig_handle:
                    lead.ig_handle="@"+username
                    ig_count+=1
                if followers:
                    lead.ig_followers=followers
                # Instagram rows use Instagram audience as primary audience.
                if lead.platform=="Instagram" and followers:
                    lead.followers=followers
                if lead.platform=="Instagram" and not lead.profile_url:
                    lead.profile_url=candidate.get("url") or f"https://www.instagram.com/{username}/"

                if not lead.email:
                    public_email=_public_email_from_ig(candidate)
                    if public_email:
                        lead.email=public_email; lead.email_confidence=1.0; email_count+=1

            if has_hunter and not lead.email:
                source_text=(lead.evidence or "")
                if candidate:
                    source_text += " " + " ".join(_ig_urls(candidate))
                    source_text += " " + (candidate.get("biography") or candidate.get("bio") or "")
                domain=_extract_domain(source_text)
                if domain:
                    email,conf=hunter_domain_lookup(domain)
                    if email:
                        lead.email=email; lead.email_confidence=conf; email_count+=1

            if lead.ig_handle and lead.score<100:
                lead.score=min(100,lead.score+3)
            if lead.email and lead.score<100:
                lead.score=min(100,lead.score+5)
            lead.updated_at=datetime.utcnow()

        db.commit()
        return {"instagram":ig_count,"email":email_count,"checked":checked}
# ---------- End live enrichment helpers ----------

def pipeline_job(job_id:int,wid:int,payload:dict):
    with get_db() as db:
        j=db.get(Job,job_id);j.status="running";j.progress=10;j.message="Starting live discovery";db.commit()

    yt_key=os.getenv("YOUTUBE_API_KEY","").strip()
    apify_key=os.getenv("APIFY_API_TOKEN","").strip()
    hunter_key=os.getenv("HUNTER_API_KEY","").strip()
    if not yt_key and not apify_key:
        with get_db() as db:
            j=db.get(Job,job_id);j.status="needs_credentials";j.progress=0;j.message="Add YOUTUBE_API_KEY and/or APIFY_API_TOKEN in .env.";j.finished_at=datetime.utcnow();db.commit()
        return

    yt_n=0; ig_n=0
    try:
        if yt_key:
            out=BASE/"data"/f"workspace_{wid}_job_{job_id}.csv"
            cmd=[sys.executable,str(BASE/"engine"/"youtube_lead_finder.py"),"--keywords",payload.get("keywords","business coach"),"--min-subs",str(payload.get("min_followers",5000)),"--max-subs",str(payload.get("max_followers",500000)),"--min-views-30d",str(payload.get("min_views",20000)),"--out",str(out)]
            env=os.environ.copy();env["PYTHONPATH"]=str(BASE/"engine")
            p=subprocess.run(cmd,cwd=BASE/"engine",env=env,capture_output=True,text=True,timeout=900)
            if p.returncode!=0:
                raise RuntimeError("YouTube discovery failed: "+(p.stderr or p.stdout)[-1200:])

            with get_db() as db:
                j=db.get(Job,job_id);j.progress=45;j.message="Importing YouTube results";db.commit()
            with open(out,newline="",encoding="utf-8") as f:
                rows=list(csv.DictReader(f))
            with get_db() as db:
                for r in rows:
                    ext=r.get("channel_id","")
                    if not ext: continue
                    exists=db.scalar(select(Lead).where(Lead.workspace_id==wid,Lead.platform=="YouTube",Lead.external_id==ext))
                    if exists:
                        # Refresh live metrics instead of silently skipping an existing lead.
                        exists.name=r.get("channel","") or exists.name
                        exists.profile_url=r.get("channel_url","") or exists.profile_url
                        exists.followers=int(float(r.get("subscribers") or exists.followers or 0))
                        exists.views_30d=int(float(r.get("views_last_30d") or exists.views_30d or 0))
                        exists.region=r.get("country","") or exists.region
                        exists.coaching_confidence=float(r.get("coaching_confidence") or exists.coaching_confidence or 0)
                        exists.faceless_screen=r.get("faceless_screen","") or exists.faceless_screen
                        exists.evidence=r.get("description_snippet","") or exists.evidence
                        exists.score=int(float(r.get("score") or exists.score or 0))
                        exists.updated_at=datetime.utcnow()
                        link_job_lead(db,job_id,exists.id)
                        continue
                    lead=Lead(workspace_id=wid,platform="YouTube",external_id=ext,name=r.get("channel",""),handle="",profile_url=r.get("channel_url",""),
                                niche=payload.get("niche","Unclassified"),followers=int(float(r.get("subscribers") or 0)),views_30d=int(float(r.get("views_last_30d") or 0)),
                                region=r.get("country",""),language="English",sells_coaching=(r.get("sells_coaching","").lower()=="yes"),
                                coaching_confidence=float(r.get("coaching_confidence") or 0),faceless_screen=r.get("faceless_screen","uncertain"),
                                email=r.get("email",""),ig_handle=r.get("ig_handle",""),ig_followers=int(float(r.get("ig_followers") or 0)),
                                score=int(float(r.get("score") or 0)),status="New",evidence=r.get("description_snippet",""),source="youtube_api")
                    db.add(lead); db.flush()
                    link_job_lead(db,job_id,lead.id)
                    yt_n+=1
                db.commit()

        if apify_key:
            with get_db() as db:
                j=db.get(Job,job_id);j.progress=62;j.message="Searching live Instagram profiles";db.commit()
            ig_n=import_instagram_leads(wid,payload,job_id)

        with get_db() as db:
            j=db.get(Job,job_id);j.progress=78;j.message="Matching Instagram profiles and enriching emails";db.commit()
        stats=enrich_workspace_leads(wid,max_leads=20)

        total=yt_n+ig_n
        with get_db() as db:
            w=db.get(Workspace,wid);w.used_this_month += total
            j=db.get(Job,job_id);j.status="completed";j.progress=100
            extras=[]
            if apify_key: extras.append(f"Instagram matched: {stats['instagram']}")
            if hunter_key: extras.append(f"emails found: {stats['email']}")
            suffix=(" · "+", ".join(extras)) if extras else ""
            j.message=f"Imported {total} live leads — YouTube: {yt_n}, Instagram: {ig_n}{suffix}"
            j.finished_at=datetime.utcnow()
            activity(db,wid,"Discovery completed",j.message);db.commit()
    except Exception as e:
        with get_db() as db:
            j=db.get(Job,job_id);j.status="failed";j.progress=0;j.message=str(e)[:1200];j.finished_at=datetime.utcnow();db.commit()

@app.get("/sources",response_class=HTMLResponse)
def sources_page(request:Request):
    u,w=require(request)
    with get_db() as db:
        jobs=db.scalars(select(Job).where(Job.workspace_id==w.id).order_by(Job.id.desc()).limit(20)).all()
        result_counts={
            j.id: db.scalar(select(func.count()).select_from(JobLead).where(JobLead.job_id==j.id)) or 0
            for j in jobs
        }
    configured={
        "youtube":bool(os.getenv("YOUTUBE_API_KEY")),
        "hunter":bool(os.getenv("HUNTER_API_KEY")),
        "instagram":bool(os.getenv("APIFY_API_TOKEN") or (os.getenv("IG_ACCESS_TOKEN") and os.getenv("IG_USER_ID")))
    }
    return templates.TemplateResponse(
        "sources.html",
        ctx(request,"sources",jobs=jobs,configured=configured,result_counts=result_counts)
    )

@app.get("/api/jobs")
def jobs_api(request:Request):
    u,w=require(request)
    with get_db() as db:
        jobs=db.scalars(select(Job).where(Job.workspace_id==w.id).order_by(Job.id.desc()).limit(20)).all()
        return [{
            "id":j.id,
            "job_type":j.job_type,
            "status":j.status,
            "progress":j.progress,
            "message":j.message,
            "created_at":j.created_at.isoformat() if j.created_at else None,
            "finished_at":j.finished_at.isoformat() if j.finished_at else None,
            "result_count":db.scalar(select(func.count()).select_from(JobLead).where(JobLead.job_id==j.id)) or 0
        } for j in jobs]

@app.post("/jobs/discovery")
def create_job(request:Request,background_tasks:BackgroundTasks,keywords:str=Form(...),niche:str=Form("Unclassified"),min_followers:int=Form(5000),max_followers:int=Form(500000),min_views:int=Form(20000)):
    u,w=require(request)
    if w.subscription_status not in ACTIVE_SUBSCRIPTION_STATUSES or w.monthly_limit<=0:
        return RedirectResponse("/billing?subscription_required=1",303)
    if w.used_this_month>=w.monthly_limit:
        return RedirectResponse("/billing?limit=1",303)
    payload={"keywords":keywords,"niche":niche,"min_followers":min_followers,"max_followers":max_followers,"min_views":min_views}
    with get_db() as db:
        j=Job(workspace_id=w.id,job_type="discovery",status="queued",progress=0,message="Queued",payload_json=json.dumps(payload))
        db.add(j);db.commit();db.refresh(j);jid=j.id
    background_tasks.add_task(pipeline_job,jid,w.id,payload)
    return RedirectResponse("/sources",303)

@app.get("/settings",response_class=HTMLResponse)
def settings_page(request:Request):
    u,w=require(request)
    return templates.TemplateResponse("settings.html",ctx(request,"settings",configured={"youtube":bool(os.getenv("YOUTUBE_API_KEY")),"hunter":bool(os.getenv("HUNTER_API_KEY")),"instagram":bool(os.getenv("APIFY_API_TOKEN") or (os.getenv("IG_ACCESS_TOKEN") and os.getenv("IG_USER_ID")))}))

@app.get("/billing",response_class=HTMLResponse)
def billing_page(request:Request):
    u,w=require(request)
    notice=""
    session_id=request.query_params.get("session_id","").strip()

    # Immediate post-checkout synchronization. Webhooks remain the source of truth,
    # but this gives the customer instant access after returning from Checkout.
    if session_id and os.getenv("STRIPE_SECRET_KEY"):
        try:
            stripe.api_key=os.getenv("STRIPE_SECRET_KEY")
            session=stripe.checkout.Session.retrieve(session_id)
            metadata=_stripe_obj_get(session,"metadata",{}) or {}
            if str(metadata.get("workspace_id",""))==str(w.id):
                sub_id=_stripe_obj_get(session,"subscription","")
                if sub_id:
                    subscription=stripe.Subscription.retrieve(sub_id)
                    with get_db() as db:
                        ww=db.get(Workspace,w.id)
                        apply_stripe_subscription(db,ww,subscription)
                        activity(db,w.id,"Stripe subscription activated",ww.plan)
                        db.commit()
                    w=workspace_for_user(u.id)
                    notice="Subscription activated successfully."
        except Exception as e:
            print(f"Checkout return sync failed: {e}")

    if request.query_params.get("canceled"):
        notice="Checkout was canceled. No charge was made."
    elif request.query_params.get("welcome"):
        notice="Welcome to LeadPilot. Choose a plan to activate your workspace and start live discovery."
    elif request.query_params.get("subscription_required"):
        notice="Choose a plan to activate your workspace and start live discovery."
    elif request.query_params.get("limit"):
        notice="You have reached this month's lead allowance."
    elif request.query_params.get("missing_stripe"):
        notice="Stripe is not fully configured yet."

    stripe_ready=bool(
        os.getenv("STRIPE_SECRET_KEY")
        and all(stripe_price_ids().values())
    )
    portal_ready=bool(stripe_ready and w.stripe_customer_id)
    return templates.TemplateResponse(
        "billing.html",
        ctx(request,"billing",stripe_ready=stripe_ready,portal_ready=portal_ready,notice=notice)
    )

@app.post("/billing/checkout")
def checkout(request:Request,plan:str=Form(...)):
    u,w=require(request)
    if plan not in PLANS:
        raise HTTPException(400,"Unknown plan")

    key=os.getenv("STRIPE_SECRET_KEY","").strip()
    price=stripe_price_ids().get(plan,"")
    if not key or not price:
        return RedirectResponse(f"/billing?missing_stripe=1&plan={plan}",303)

    stripe.api_key=key

    with get_db() as db:
        ww=db.get(Workspace,w.id)
        if not ww.stripe_customer_id:
            customer=stripe.Customer.create(
                email=u.email,
                name=u.full_name or None,
                metadata={"workspace_id":str(w.id)}
            )
            ww.stripe_customer_id=customer.id
            db.commit()
        customer_id=ww.stripe_customer_id

    session=stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        client_reference_id=str(w.id),
        line_items=[{"price":price,"quantity":1}],
        metadata={"workspace_id":str(w.id),"plan":plan},
        subscription_data={"metadata":{"workspace_id":str(w.id),"plan":plan}},
        success_url=app_url()+"/billing?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=app_url()+"/billing?canceled=1",
        allow_promotion_codes=True,
    )
    return RedirectResponse(session.url,303)

@app.post("/billing/portal")
def billing_portal(request:Request):
    u,w=require(request)
    key=os.getenv("STRIPE_SECRET_KEY","").strip()
    if not key or not w.stripe_customer_id:
        return RedirectResponse("/billing?missing_stripe=1",303)
    stripe.api_key=key
    portal=stripe.billing_portal.Session.create(
        customer=w.stripe_customer_id,
        return_url=app_url()+"/billing"
    )
    return RedirectResponse(portal.url,303)

@app.post("/stripe/webhook")
async def stripe_webhook(request:Request):
    secret=os.getenv("STRIPE_WEBHOOK_SECRET","").strip()
    key=os.getenv("STRIPE_SECRET_KEY","").strip()
    if not secret or not key:
        raise HTTPException(503,"Stripe webhook is not configured")

    payload=await request.body()
    signature=request.headers.get("stripe-signature","")
    stripe.api_key=key

    try:
        event=stripe.Webhook.construct_event(payload,signature,secret)
    except Exception as e:
        print(f"Stripe webhook signature error: {e}")
        raise HTTPException(400,"Invalid Stripe webhook")

    event_id=_stripe_obj_get(event,"id","")
    event_type=_stripe_obj_get(event,"type","")
    data=_stripe_obj_get(event,"data",{}) or {}
    obj=_stripe_obj_get(data,"object",{}) or {}

    with get_db() as db:
        if event_id and db.scalar(select(StripeEvent).where(StripeEvent.event_id==event_id)):
            return JSONResponse({"received":True,"duplicate":True})

        try:
            if event_type=="checkout.session.completed":
                workspace=workspace_for_stripe_object(db,obj)
                if workspace:
                    customer=_stripe_obj_get(obj,"customer","") or ""
                    subscription_id=_stripe_obj_get(obj,"subscription","") or ""
                    if customer:
                        workspace.stripe_customer_id=str(customer)
                    if subscription_id:
                        subscription=stripe.Subscription.retrieve(subscription_id)
                        apply_stripe_subscription(db,workspace,subscription)
                        activity(db,workspace.id,"Stripe checkout completed",workspace.plan)

            elif event_type in ("customer.subscription.created","customer.subscription.updated","customer.subscription.deleted"):
                workspace=workspace_for_stripe_object(db,obj)
                if workspace:
                    apply_stripe_subscription(db,workspace,obj)
                    activity(db,workspace.id,"Subscription updated",workspace.subscription_status)

            elif event_type=="invoice.paid":
                customer=_stripe_obj_get(obj,"customer","") or ""
                workspace=db.scalar(select(Workspace).where(Workspace.stripe_customer_id==str(customer))) if customer else None
                if workspace:
                    billing_reason=_stripe_obj_get(obj,"billing_reason","") or ""
                    if billing_reason=="subscription_cycle":
                        workspace.used_this_month=0
                        activity(db,workspace.id,"Monthly lead allowance reset",workspace.plan)

                    subscription_id=_stripe_obj_get(obj,"subscription","") or workspace.stripe_subscription_id
                    if subscription_id:
                        try:
                            subscription=stripe.Subscription.retrieve(subscription_id)
                            apply_stripe_subscription(db,workspace,subscription)
                        except Exception as e:
                            print(f"Invoice subscription sync failed: {e}")

            elif event_type=="invoice.payment_failed":
                customer=_stripe_obj_get(obj,"customer","") or ""
                workspace=db.scalar(select(Workspace).where(Workspace.stripe_customer_id==str(customer))) if customer else None
                if workspace:
                    workspace.subscription_status="past_due"
                    activity(db,workspace.id,"Stripe payment failed","Billing action required")

            if event_id:
                db.add(StripeEvent(event_id=str(event_id),event_type=str(event_type)))
            db.commit()

        except Exception:
            db.rollback()
            raise

    return JSONResponse({"received":True})

def _export_rows(db, wid:int, run_id:int=0):
    q=select(Lead).where(Lead.workspace_id==wid)
    if run_id>0:
        q=q.join(JobLead, JobLead.lead_id==Lead.id).where(JobLead.job_id==run_id)
    return db.scalars(q.order_by(Lead.score.desc())).all()

EXPORT_FIELDS=[
    "name","handle","platform","niche","followers","views_30d","region","language",
    "sells_coaching","coaching_confidence","faceless_screen","email","email_confidence",
    "ig_handle","ig_followers","score","status","profile_url","source"
]

@app.get("/export.csv")
def export_csv(request:Request,run_id:int=0):
    u,w=require(request)
    with get_db() as db:
        rows=_export_rows(db,w.id,run_id)
    s=io.StringIO()
    wr=csv.writer(s); wr.writerow(EXPORT_FIELDS)
    for l in rows:
        wr.writerow([getattr(l,f) for f in EXPORT_FIELDS])
    suffix=f"-run-{run_id}" if run_id>0 else ""
    return StreamingResponse(iter([s.getvalue()]),media_type="text/csv",headers={"Content-Disposition":f"attachment; filename=leadpilot-leads{suffix}.csv"})

@app.get("/export.xlsx")
def export_xlsx(request:Request,run_id:int=0):
    u,w=require(request)
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    with get_db() as db:
        rows=_export_rows(db,w.id,run_id)

    wb=Workbook(); ws=wb.active; ws.title="Leads"
    ws.append(EXPORT_FIELDS)
    for cell in ws[1]:
        cell.font=Font(bold=True)
        cell.fill=PatternFill("solid", fgColor="EDEDFC")
        cell.alignment=Alignment(vertical="center")
    for l in rows:
        ws.append([getattr(l,f) for f in EXPORT_FIELDS])
    ws.freeze_panes="A2"; ws.auto_filter.ref=ws.dimensions
    for idx,field in enumerate(EXPORT_FIELDS,1):
        width=max(len(field)+2,12)
        for cell in ws[get_column_letter(idx)][1:60]:
            width=max(width,min(42,len(str(cell.value or ""))+2))
        ws.column_dimensions[get_column_letter(idx)].width=width
    buf=io.BytesIO(); wb.save(buf); buf.seek(0)
    suffix=f"-run-{run_id}" if run_id>0 else ""
    return StreamingResponse(buf,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f"attachment; filename=leadpilot-leads{suffix}.xlsx"})

@app.get("/jobs/{job_id}/export.xlsx")
def export_job_xlsx(request:Request,job_id:int):
    u,w=require(request)
    with get_db() as db:
        j=db.get(Job,job_id)
        if not j or j.workspace_id!=w.id:
            raise HTTPException(404)
    return export_xlsx(request,run_id=job_id)
