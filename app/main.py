"""Employee attendance API.  All application code intentionally lives here."""
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]
IST = timezone(timedelta(hours=5, minutes=30))
PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

def startup_indexes():
    try:
        db.employees.create_index([("emp_code", ASCENDING)], unique=True)
        db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)])
        db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)])
        db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True)
        db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)])
        db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING), ("status", ASCENDING)])
        db.attendance_logs.create_index([("date", ASCENDING), ("status", ASCENDING)])
    except Exception:
        pass
@app.on_event("startup")
def _startup(): startup_indexes()

def epoch(v: int) -> datetime:
    if type(v) is not int or v < 100000000000 or v > 4102444800000:
        raise ValueError("must be epoch milliseconds")
    return datetime.fromtimestamp(v // 1000, timezone.utc).replace(microsecond=0).replace(tzinfo=None)
def ms(v):
    if v is None: return None
    if v.tzinfo is None: v = v.replace(tzinfo=timezone.utc)
    return int(v.timestamp() * 1000)
def now_dt(): return datetime.now(timezone.utc).replace(microsecond=0).replace(tzinfo=None)
def cal(v: str) -> date:
    try: return date.fromisoformat(v)
    except Exception: raise ValueError("invalid date")
def month(v: str) -> date:
    if len(v) != 7 or v[4] != "-": raise ValueError("invalid month")
    d = cal(v + "-01")
    return d
def shift(v: str) -> tuple[int,int]:
    if len(v) != 5 or v[2] != ":" or not v[:2].isdigit() or not v[3:].isdigit(): raise ValueError("invalid shift")
    h,m = int(v[:2]),int(v[3:])
    if h > 23 or m > 59: raise ValueError("invalid shift")
    return h,m
def qdate(v, name="date"):
    try: return cal(v)
    except ValueError: raise HTTPException(422, f"invalid {name}")
def qmonth(v):
    try: return month(v)
    except ValueError: raise HTTPException(422, "invalid month")
def round2(x): return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
def round4(x): return float(Decimal(str(x)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
def attendance_date(dt, emp):
    local = dt.replace(tzinfo=timezone.utc).astimezone(IST)
    h,m = shift(emp["shift_start"]); eh,em = shift(emp["shift_end"])
    d = local.date()
    if (eh,em) <= (h,m) and (local.hour,local.minute) < (eh,em): d -= timedelta(days=1)
    return d.isoformat()
def shift_end_dt(rec, emp):
    d = cal(rec["date"]); h,m = shift(emp["shift_start"]); eh,em = shift(emp["shift_end"])
    end = datetime.combine(d, time(eh,m), IST)
    if (eh,em) <= (h,m): end += timedelta(days=1)
    return end.astimezone(timezone.utc).replace(tzinfo=None)
def derived(pin, pout, emp, recdate):
    late = 0
    if pin:
        local = pin.replace(tzinfo=timezone.utc).astimezone(IST)
        h,m = shift(emp["shift_start"])
        start = datetime.combine(local.date(), time(h,m), IST).astimezone(timezone.utc).replace(tzinfo=None)
        diff = int((pin-start).total_seconds()//60)
        late = diff if diff > 10 else 0
    if not pout: return None, late, 0, False
    hours = round2((pout-pin).total_seconds()/3600)
    end = shift_end_dt({"date": recdate}, emp)
    od = int((pout-end).total_seconds()//60)
    return hours, late, od if od >= 30 else 0, hours < 4.5
def serialize_history(h):
    out=[]
    for x in h or []:
        y=dict(x); y["at"]=ms(y["at"])
        for c in y.get("changes",{}).values():
            if isinstance(c.get("from"), datetime): c["from"]=ms(c["from"])
            if isinstance(c.get("to"), datetime): c["to"]=ms(c["to"])
        out.append(y)
    return out
def serialize(d):
    d=dict(d); d.pop("_id",None)
    for k in ("created_at","punch_in","punch_out"):
        if isinstance(d.get(k), datetime): d[k]=ms(d[k])
    if "history" in d: d["history"]=serialize_history(d["history"])
    for k,v in {"late_minutes":0,"overtime_minutes":0,"half_day":False}.items(): d.setdefault(k,v)
    return d

class EmployeeIn(BaseModel):
    model_config=ConfigDict(extra="ignore")
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1,max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1,max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str
    @field_validator("joined_on")
    @classmethod
    def valid_date(cls,v): cal(v); return v
    @model_validator(mode="after")
    def shifts(self):
        if self.shift_start == self.shift_end: raise ValueError("shifts must differ")
        return self
class PunchInIn(BaseModel):
    model_config=ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[int]=None
    status: Literal["PRESENT","WFH","ON_DUTY"]="PRESENT"
    @field_validator("punched_at")
    @classmethod
    def valid_ts(cls,v): return v if v is None else (epoch(v) and v)
class PunchOutIn(PunchInIn):
    status: Literal["PRESENT"]="PRESENT"
class RegIn(BaseModel):
    model_config=ConfigDict(extra="ignore")
    status: Optional[Literal["PRESENT","WFH","ON_DUTY","ABSENT","LEAVE"]]=None
    punch_in: Optional[int]=None
    punch_out: Optional[int]=None
    reason: str=Field(min_length=5,max_length=200)
    regularized_by: str=Field(min_length=1,max_length=50)
    @field_validator("punch_in","punch_out")
    @classmethod
    def valid_ts(cls,v): return v if v is None else (epoch(v) and v)

@app.get("/health")
def health():
    try: client.admin.command("ping"); return {"status":"ok"}
    except Exception: raise HTTPException(503, "database unavailable")
@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc=body.model_dump(); doc["created_at"]=now_dt()
    try: db.employees.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,"emp_code already exists")
    return serialize(doc)
@app.get("/employees")
def list_employees(department:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={} if department is None else {"department":department}; total=db.employees.count_documents(q)
    docs=db.employees.find(q,{"_id":0}).sort("emp_code",ASCENDING).skip((page-1)*page_size).limit(page_size)
    return {"items":[serialize(x) for x in docs],"total":total,"page":page,"page_size":page_size}
@app.post("/attendance/punch-in",status_code=201)
def punch_in(body:PunchInIn):
    emp=db.employees.find_one({"emp_code":body.emp_code})
    if not emp: raise HTTPException(404,"employee not found")
    dt=epoch(body.punched_at) if body.punched_at is not None else now_dt(); d=attendance_date(dt,emp)
    doc={"emp_code":body.emp_code,"date":d,"status":body.status,"punch_in":dt,"punch_out":None,"work_hours":None,"late_minutes":derived(dt,None,emp,d)[1],"overtime_minutes":0,"half_day":False,"history":[]}
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,"already punched in for this date")
    return serialize(doc)
@app.post("/attendance/punch-out")
def punch_out(body:PunchOutIn):
    emp=db.employees.find_one({"emp_code":body.emp_code})
    if not emp: raise HTTPException(404,"employee not found")
    dt=epoch(body.punched_at) if body.punched_at is not None else now_dt()
    rec=db.attendance_logs.find_one({"emp_code":body.emp_code,"punch_in":{"$lte":dt},"punch_out":None},sort=[("punch_in",-1)])
    if not rec:
        closed=db.attendance_logs.find_one({"emp_code":body.emp_code,"punch_in":{"$lte":dt},"punch_out":{"$ne":None}},sort=[("punch_in",-1)])
        if closed: raise HTTPException(409,"record already punched out")
        raise HTTPException(404,"no open punch-in found")
    if dt <= rec["punch_in"] or dt > rec["punch_in"]+timedelta(hours=24): raise HTTPException(422,"invalid punch-out time")
    wh,late,ot,half=derived(rec["punch_in"],dt,emp,rec["date"])
    updated=db.attendance_logs.find_one_and_update({"_id":rec["_id"],"punch_out":None},{"$set":{"punch_out":dt,"work_hours":wh,"late_minutes":late,"overtime_minutes":ot,"half_day":half}},return_document=True)
    if not updated: raise HTTPException(409,"record already punched out")
    return serialize(updated)
@app.get("/attendance")
def list_attendance(emp_code:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[Literal["PRESENT","WFH","ON_DUTY","ABSENT","LEAVE"]]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    if date_from: qdate(date_from,"date_from")
    if date_to: qdate(date_to,"date_to")
    if date_from and date_to and date_from>date_to: raise HTTPException(422,"date_from must not exceed date_to")
    q={}; 
    if emp_code:q["emp_code"]=emp_code
    if status:q["status"]=status
    if date_from or date_to:q["date"]={k:v for k,v in [("$gte",date_from),("$lte",date_to)] if v}
    total=db.attendance_logs.count_documents(q); docs=db.attendance_logs.find(q).sort([("date",-1),("emp_code",1)]).skip((page-1)*page_size).limit(page_size)
    return {"items":[serialize(x) for x in docs],"total":total,"page":page,"page_size":page_size}
@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code:str,date:str,body:RegIn):
    qdate(date); emp=db.employees.find_one({"emp_code":emp_code})
    rec=db.attendance_logs.find_one({"emp_code":emp_code,"date":date})
    if not emp or not rec: raise HTTPException(404,"employee or attendance record not found")
    final={"status":rec.get("status"),"punch_in":rec.get("punch_in"),"punch_out":rec.get("punch_out")}
    supplied=body.model_dump(exclude_unset=True)
    if body.status in {"ABSENT","LEAVE"} and ("punch_in" in supplied or "punch_out" in supplied): raise HTTPException(422,"absence cannot have punches")
    for k in ("status","punch_in","punch_out"):
        if k in supplied: final[k]=supplied[k] if k=="status" else (epoch(supplied[k]) if supplied[k] is not None else None)
    if final["status"] in PRESENCE and not final["punch_in"]: raise HTTPException(422,"presence requires punch_in")
    if final["status"] in {"ABSENT","LEAVE"}: final["punch_in"]=final["punch_out"]=None
    if final["punch_in"] and attendance_date(final["punch_in"],emp)!=date: raise HTTPException(422,"punch_in date mismatch")
    if final["punch_out"] and (not final["punch_in"] or final["punch_out"]<=final["punch_in"] or final["punch_out"]>final["punch_in"]+timedelta(hours=24)): raise HTTPException(422,"invalid punch_out")
    wh,late,ot,half=derived(final["punch_in"],final["punch_out"],emp,date) if final["status"] in PRESENCE else (None,0,0,False)
    values={**final,"work_hours":wh,"late_minutes":late,"overtime_minutes":ot,"half_day":half}
    changes={}
    for k,v in values.items():
        old=rec.get(k,0 if k in ("late_minutes","overtime_minutes") else False if k=="half_day" else None)
        if old!=v: changes[k]={"from":old,"to":v}
    if not changes: raise HTTPException(422,"request changes nothing")
    hist={"at":now_dt(),"by":body.regularized_by,"reason":body.reason,"changes":changes}
    updated=db.attendance_logs.find_one_and_update({"_id":rec["_id"],"history":rec.get("history",[])},{"$set":values,"$push":{"history":hist}},return_document=True)
    if not updated: raise HTTPException(409,"record changed concurrently")
    return serialize(updated)

def month_bounds(m):
    first=m; last=(m.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
    return first,last
def logs_for(emp, first, last):
    return db.attendance_logs.find({"emp_code":emp,"date":{"$gte":first.isoformat(),"$lte":last.isoformat()}})
def workdays(first,last,joined=None):
    n=0; d=first
    while d<=last:
        if d.weekday()<5 and (joined is None or d>=joined): n+=1
        d+=timedelta(days=1)
    return n
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code:str,month:str):
    m=qmonth(month); emp=db.employees.find_one({"emp_code":emp_code})
    if not emp: raise HTTPException(404,"employee not found")
    first,last=month_bounds(m); rows=list(logs_for(emp_code,first,last))
    present=sum((0.5 if r.get("half_day",False) else 1) for r in rows if r.get("status") in PRESENCE and cal(r["date"]).weekday()<5)
    late=sum(1 for r in rows if r.get("late_minutes",0)>0)
    out={"emp_code":emp_code,"month":month,"working_days":workdays(first,last,cal(emp["joined_on"])),"present_days":present,"leave_days":sum(r.get("status")=="LEAVE" for r in rows),"late_count":late,"total_late_minutes":sum(r.get("late_minutes",0) for r in rows),"total_overtime_minutes":sum(r.get("overtime_minutes",0) for r in rows)}
    out["attendance_pct"]=None if not out["working_days"] else round4(out["present_days"]/out["working_days"]*100)
    return out
@app.get("/analytics/departments/summary")
def department_summary(month:str,department:Optional[str]=None):
    m=qmonth(month); first,last=month_bounds(m); q={"joined_on":{"$lte":last.isoformat()}}
    if department is not None:q["department"]=department
    groups={}
    for e in db.employees.find(q):
        g=groups.setdefault(e["department"],{"department":e["department"],"headcount":0,"present_days":0.0,"hours":[],"late_count":0,"total_late_minutes":0,"leave_count":0,"on_duty_count":0}); g["headcount"]+=1
        for r in logs_for(e["emp_code"],first,last):
            if r.get("status") in PRESENCE and cal(r["date"]).weekday()<5:g["present_days"]+=.5 if r.get("half_day") else 1
            if r.get("status") in PRESENCE and r.get("work_hours") is not None:g["hours"].append(r["work_hours"])
            if r.get("late_minutes",0)>0:g["late_count"]+=1;g["total_late_minutes"]+=r.get("late_minutes",0)
            if r.get("status")=="LEAVE":g["leave_count"]+=1
            if r.get("status")=="ON_DUTY":g["on_duty_count"]+=1
    items=[]
    for g in sorted(groups.values(),key=lambda x:x["department"]):
        g["avg_work_hours"]=round2(sum(g.pop("hours"))/len(g["hours"])) if g["hours"] else None; items.append(g)
    return {"month":month,"items":items}
@app.get("/analytics/leaderboard/late")
def leaderboard(month:str,limit:int=Query(10,ge=1,le=50),department:Optional[str]=None):
    m=qmonth(month); first,last=month_bounds(m); groups={}
    for r in logs_for("",first,last) if False else db.attendance_logs.find({"date":{"$gte":first.isoformat(),"$lte":last.isoformat()},"late_minutes":{"$gt":0}}):
        e=db.employees.find_one({"emp_code":r["emp_code"]})
        if not e or (department is not None and e["department"]!=department):continue
        g=groups.setdefault(r["emp_code"],{"emp_code":r["emp_code"],"name":e["name"],"department":e["department"],"total_late_minutes":0,"late_count":0});g["total_late_minutes"]+=r.get("late_minutes",0);g["late_count"]+=1
    arr=sorted(groups.values(),key=lambda x:(-x["total_late_minutes"],x["emp_code"])); prev=None
    for i,g in enumerate(arr): g["rank"]=i+1 if prev==g["total_late_minutes"] else i+1; prev=g["total_late_minutes"]
    # standard competition rank (index of first row in each tied group)
    for i,g in enumerate(arr):
        g["rank"]=next(j+1 for j,x in enumerate(arr) if x["total_late_minutes"]==g["total_late_minutes"])
    return {"month":month,"items":[g for g in arr if g["rank"]<=limit]}
@app.get("/analytics/departments/{department}/trend")
def department_trend(department:str,from_:str=Query(...,alias="from"),to:str=Query(...)):
    start=qdate(from_,"from"); end=qdate(to,"to")
    if end<start or (end-start).days+1>92: raise HTTPException(422,"invalid trend range")
    if not db.employees.find_one({"department":department}):raise HTTPException(404,"department not found")
    emps=list(db.employees.find({"department":department})); items=[]; rates=[]
    d=start
    while d<=end:
        ds=d.isoformat(); eligible=[e for e in emps if cal(e["joined_on"])<=d]; rows=list(db.attendance_logs.find({"emp_code":{"$in":[e["emp_code"] for e in emps]},"date":ds}))
        pc=sum(.5 if r.get("half_day") else 1 for r in rows if r.get("status") in PRESENCE); lc=sum(r.get("late_minutes",0)>0 for r in rows)
        rate=round4(pc/len(eligible)) if d.weekday()<5 and eligible else (0.0 if d.weekday()<5 and eligible else None); rates.append(rate)
        vals=[x for x in rates[-7:] if x is not None]; avg=round4(sum(vals)/len(vals)) if vals else None
        items.append({"date":ds,"is_working_day":d.weekday()<5,"headcount":len(eligible),"present_count":pc,"late_count":lc,"attendance_rate":rate,"moving_avg_7d":avg});d+=timedelta(days=1)
    return {"department":department,"items":items}
@app.get("/admin/explain/{endpoint}")
def explain(endpoint:str,emp_code:Optional[str]=None,month:Optional[str]=None,department:Optional[str]=None,limit:int=10,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,from_:Optional[str]=Query(None,alias="from"),to:Optional[str]=None,page:int=1,page_size:int=20):
    if endpoint not in {"attendance_list","employee_monthly","department_summary","late_leaderboard","department_trend"}: raise HTTPException(422,"unsupported endpoint")
    if endpoint=="attendance_list":
        q={}
        if emp_code:q["emp_code"]=emp_code
        if status:q["status"]=status
        if date_from or date_to:q["date"]={k:v for k,v in [("$gte",date_from),("$lte",date_to)] if v}
        exp=db.command("explain",{"find":"attendance_logs","filter":q,"sort":{"date":-1,"emp_code":1},"skip":(page-1)*page_size,"limit":page_size},verbosity="executionStats"); coll="attendance_logs"
    else:
        # Explain an indexed base query; aggregation explanations vary by MongoDB version.
        if endpoint in {"employee_monthly", "department_summary", "late_leaderboard"} and not month:
            raise HTTPException(422, "month is required")
        if endpoint == "employee_monthly" and not emp_code:
            raise HTTPException(422, "emp_code is required")
        if endpoint == "department_trend" and (not department or not from_ or not to):
            raise HTTPException(422, "department, from and to are required")
        q={}
        if endpoint == "employee_monthly":
            m=qmonth(month); first,last=month_bounds(m)
            q={"emp_code":emp_code,"date":{"$gte":first.isoformat(),"$lte":last.isoformat()}}
        elif endpoint == "late_leaderboard":
            m=qmonth(month); first,last=month_bounds(m)
            q={"date":{"$gte":first.isoformat(),"$lte":last.isoformat()},"late_minutes":{"$gt":0}}
        elif endpoint == "department_summary":
            m=qmonth(month); q={"joined_on":{"$lte":month_bounds(m)[1].isoformat()}}
            if department: q["department"]=department
        else:
            q={"department":department,"joined_on":{"$lte":to}}
        coll="employees" if endpoint in {"department_summary","department_trend"} else "attendance_logs"
        exp=db.command("explain",{"find":coll,"filter":q},verbosity="executionStats")
    return {"endpoint":endpoint,"collection":coll,"explain":exp}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return HTMLResponse("""<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Attendance Hub</title>
<style>
:root{--b:#3157d5;--ink:#182238;--muted:#6b7588;--line:#e5e9f1;--bg:#f5f7fb}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px system-ui,sans-serif}.app{display:flex;min-height:100vh}.side{width:225px;background:#111a33;color:#cbd5f5;padding:24px 14px}.brand{font-weight:800;color:#fff;font-size:19px;margin:0 12px 30px}.brand b{background:#4c6fff;padding:7px 10px;border-radius:9px;margin-right:7px}.nav{width:100%;border:0;background:transparent;color:#acb8db;text-align:left;padding:11px 13px;border-radius:8px;cursor:pointer;margin:3px 0;font-size:14px}.nav:hover,.nav.active{color:#fff;background:#293760}.main{flex:1;padding:28px;min-width:0}.top{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:25px}h1{margin:0;font-size:27px}h2{font-size:18px;margin:0 0 16px}.muted,.sub{color:var(--muted)}.sub{margin:2px 0}.month{background:#fff;border:1px solid var(--line);padding:9px;border-radius:8px}.grid{display:grid;gap:16px}.stats{grid-template-columns:repeat(4,1fr);margin-bottom:20px}.card,.panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px;box-shadow:0 2px 8px #2030600b}.stat-label{color:var(--muted);font-size:12px}.stat{font-size:29px;font-weight:800;margin-top:5px}.two{grid-template-columns:1.4fr 1fr}.wide{grid-column:1/-1}input,select{border:1px solid #d5dce8;border-radius:7px;padding:9px;background:#fff;color:var(--ink)}button{border:0;border-radius:7px;padding:9px 13px;cursor:pointer;font-weight:650}.primary{background:var(--b);color:#fff}.ghost{background:#edf1ff;color:#2945a5}.toolbar{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:15px}.table{overflow:auto}table{width:100%;border-collapse:collapse;min-width:560px}th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);text-align:left;border-bottom:1px solid var(--line);padding:9px}td{padding:11px 9px;border-bottom:1px solid #f0f2f6;white-space:nowrap}.pill{border-radius:15px;padding:3px 8px;font-size:11px;font-weight:700;background:#eef1f7}.green{background:#e4f7ed;color:#08774f}.orange{background:#fff1dc;color:#a65a0e}.red{background:#ffebeb;color:#b83030}.empty{text-align:center;color:var(--muted);padding:25px}.section{display:none}.section.active{display:block}.notice{display:none;background:#fff5d9;color:#825c00;padding:10px;border-radius:7px;margin-bottom:15px}.modal{position:fixed;inset:0;background:#111a3380;display:none;place-items:center;padding:20px}.modal.show{display:grid}.box{background:#fff;border-radius:12px;padding:22px;width:min(560px,100%)}.form{display:grid;grid-template-columns:1fr 1fr;gap:12px}.form label{font-size:12px;font-weight:700;color:var(--muted);display:block;margin-bottom:4px}.form input{width:100%}.full{grid-column:1/-1}.actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}@media(max-width:800px){.side{width:70px}.brand span,.nav span{display:none}.main{padding:18px}.stats{grid-template-columns:1fr 1fr}.two{grid-template-columns:1fr}}@media(max-width:500px){.top{align-items:flex-start;flex-direction:column}.form{grid-template-columns:1fr}.full{grid-column:auto}}
</style></head><body><div class="app"><aside class="side"><div class="brand"><b>A</b><span>Attendance Hub</span></div>
<button class="nav active" data-s="overview">▦ <span>Overview</span></button><button class="nav" data-s="employees">♙ <span>Employees</span></button><button class="nav" data-s="attendance">◷ <span>Attendance</span></button><button class="nav" data-s="analytics">⌁ <span>Analytics</span></button><button class="nav" data-s="actions">＋ <span>Quick actions</span></button></aside>
<main class="main"><div class="top"><div><h1 id="title">Overview</h1><p class="sub">Employee attendance and analytics dashboard</p></div><label class="month">Reporting month <input id="month" type="month"></label></div><div id="error" class="notice"></div>
<section id="overview" class="section active"><div class="grid stats"><div class="card"><div class="stat-label">Total employees</div><div id="se" class="stat">—</div></div><div class="card"><div class="stat-label">Attendance records</div><div id="sr" class="stat">—</div></div><div class="card"><div class="stat-label">Present records</div><div id="sp" class="stat">—</div></div><div class="card"><div class="stat-label">Late records</div><div id="sl" class="stat">—</div></div></div><div class="grid two"><div class="panel"><h2>Recent attendance</h2><div id="recent"></div></div><div class="panel"><h2>Late leaderboard</h2><div id="lead"></div></div></div></section>
<section id="employees" class="section"><div class="panel"><div style="display:flex;justify-content:space-between;gap:10px"><h2>Employees</h2><button class="primary" onclick="openModal()">+ Add employee</button></div><div class="toolbar"><input id="dept" placeholder="Filter by department" oninput="employees()"></div><div id="etable"></div></div></section>
<section id="attendance" class="section"><div class="panel"><h2>Attendance records</h2><div class="toolbar"><input id="ecode" placeholder="Employee code"><select id="status"><option value="">All statuses</option><option>PRESENT</option><option>WFH</option><option>ON_DUTY</option><option>ABSENT</option><option>LEAVE</option></select><button class="primary" onclick="attendance()">Apply filters</button></div><div id="atable"></div></div></section>
<section id="analytics" class="section"><div class="grid two"><div class="panel"><h2>Department summary</h2><div id="summary"></div></div><div class="panel"><h2>Top late employees</h2><div id="alead"></div></div><div class="panel wide"><h2>Department trend</h2><div class="toolbar"><select id="trenddept"></select><button class="primary" onclick="trend()">Load trend</button></div><div id="trend"></div></div></div></section>
<section id="actions" class="section"><div class="panel" style="max-width:600px"><h2>Quick attendance action</h2><p class="muted">Record a punch using an employee code. Server-side timezone and shift rules are applied.</p><div class="form"><div><label>Employee code</label><input id="aemp" placeholder="EMP0001"></div><div><label>Action</label><select id="atype"><option value="in">Punch in</option><option value="out">Punch out</option></select></div><div class="full"><label>Status for punch-in</label><select id="astatus"><option>PRESENT</option><option>WFH</option><option>ON_DUTY</option></select></div></div><div class="actions"><button class="primary" onclick="action()">Record attendance</button></div><p id="result" class="muted"></p></div></section></main></div>
<div id="modal" class="modal"><div class="box"><h2>Add employee</h2><div class="form"><div><label>Employee code</label><input id="ncode" placeholder="EMP0007"></div><div><label>Name</label><input id="nname"></div><div><label>Email</label><input id="nemail" type="email"></div><div><label>Department</label><input id="ndept"></div><div><label>Shift start</label><input id="nstart" type="time" value="09:30"></div><div><label>Shift end</label><input id="nend" type="time" value="18:30"></div><div><label>Joined on</label><input id="njoined" type="date"></div></div><div class="actions"><button class="ghost" onclick="closeModal()">Cancel</button><button class="primary" onclick="createEmployee()">Create employee</button></div></div></div>
<script>
const $=x=>document.getElementById(x), esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])), mon=()=> $('month').value||new Date().toISOString().slice(0,7);
async function api(u,o={}){let r=await fetch(u,o),b=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof b.detail==='string'?b.detail:'Request failed');return b}
function err(e){$('error').textContent=e.message;$('error').style.display='block';setTimeout(()=>$('error').style.display='none',5000)}
function stat(x){let c=['PRESENT','WFH','ON_DUTY'].includes(x)?'green':x==='LEAVE'?'orange':'red';return `<span class="pill ${c}">${esc(x)}</span>`}
function dt(x){return x?new Date(x).toLocaleString([],{dateStyle:'medium',timeStyle:'short'}):'—'}
function empty(id){$(id).innerHTML='<div class="empty">Loading…</div>'}
function go(s){document.querySelectorAll('.section').forEach(x=>x.classList.toggle('active',x.id===s));document.querySelectorAll('.nav').forEach(x=>x.classList.toggle('active',x.dataset.s===s));$('title').textContent=s[0].toUpperCase()+s.slice(1);if(s==='employees')employees();if(s==='attendance')attendance();if(s==='analytics')analytics()}
document.querySelectorAll('.nav').forEach(x=>x.onclick=()=>go(x.dataset.s));$('month').value=new Date().toISOString().slice(0,7);$('month').onchange=overview;
async function overview(){try{let[e,a,l]=await Promise.all([api('/employees?page_size=100'),api('/attendance?page_size=100'),api('/analytics/leaderboard/late?month='+mon()+'&limit=5')]);$('se').textContent=e.total;$('sr').textContent=a.total;$('sp').textContent=a.items.filter(x=>['PRESENT','WFH','ON_DUTY'].includes(x.status)).length;$('sl').textContent=a.items.filter(x=>x.late_minutes>0).length;$('recent').innerHTML=a.items.length?`<div class="table"><table><tr><th>Employee</th><th>Date</th><th>Status</th><th>Hours</th></tr>${a.items.slice(0,7).map(x=>`<tr><td><b>${esc(x.emp_code)}</b></td><td>${x.date}</td><td>${stat(x.status)}</td><td>${x.work_hours??'—'}</td></tr>`).join('')}</table></div>`:'<div class="empty">No records</div>';$('lead').innerHTML=l.items.length?`<div class="table"><table><tr><th>Rank</th><th>Employee</th><th>Late</th></tr>${l.items.map(x=>`<tr><td>#${x.rank}</td><td>${esc(x.name)}<br><small class="muted">${x.emp_code}</small></td><td>${x.total_late_minutes} min</td></tr>`).join('')}</table></div>`:'<div class="empty">No late records</div>'}catch(e){err(e)}}
async function employees(){empty('etable');try{let q=$('dept').value,d=await api('/employees?page_size=100'+(q?'&department='+encodeURIComponent(q):''));$('etable').innerHTML=`<div class="table"><table><tr><th>Code</th><th>Name</th><th>Email</th><th>Department</th><th>Shift</th><th>Joined</th></tr>${d.items.map(x=>`<tr><td><b>${x.emp_code}</b></td><td>${esc(x.name)}</td><td>${esc(x.email)}</td><td>${esc(x.department)}</td><td>${x.shift_start}–${x.shift_end}</td><td>${x.joined_on}</td></tr>`).join('')}</table></div>`}catch(e){err(e)}}
async function attendance(){empty('atable');try{let q='/attendance?page_size=100';if($('ecode').value)q+='&emp_code='+encodeURIComponent($('ecode').value);if($('status').value)q+='&status='+$('status').value;let d=await api(q);$('atable').innerHTML=`<div class="table"><table><tr><th>Employee</th><th>Date</th><th>Status</th><th>Punch in</th><th>Punch out</th><th>Late</th><th>Hours</th></tr>${d.items.map(x=>`<tr><td><b>${x.emp_code}</b></td><td>${x.date}</td><td>${stat(x.status)}</td><td>${dt(x.punch_in)}</td><td>${dt(x.punch_out)}</td><td>${x.late_minutes} min</td><td>${x.work_hours??'—'}</td></tr>`).join('')}</table></div>`}catch(e){err(e)}}
async function analytics(){empty('summary');empty('alead');try{let[s,l,e]=await Promise.all([api('/analytics/departments/summary?month='+mon()),api('/analytics/leaderboard/late?month='+mon()+'&limit=10'),api('/employees?page_size=100')]);$('summary').innerHTML=`<div class="table"><table><tr><th>Department</th><th>Headcount</th><th>Present</th><th>Avg hours</th></tr>${s.items.map(x=>`<tr><td><b>${esc(x.department)}</b></td><td>${x.headcount}</td><td>${x.present_days}</td><td>${x.avg_work_hours??'—'}</td></tr>`).join('')}</table></div>`;$('alead').innerHTML=l.items.length?`<div class="table"><table><tr><th>Rank</th><th>Employee</th><th>Late</th></tr>${l.items.map(x=>`<tr><td>#${x.rank}</td><td>${esc(x.name)}</td><td>${x.total_late_minutes} min</td></tr>`).join('')}</table></div>`:'<div class="empty">No late records</div>';$('trenddept').innerHTML=[...new Set(e.items.map(x=>x.department))].sort().map(x=>`<option>${esc(x)}</option>`).join('');if($('trenddept').value)trend()}catch(e){err(e)}}
async function trend(){let d=$('trenddept').value;if(!d)return;empty('trend');try{let z=new Date(mon()+'-01'),from=mon()+'-01',to=new Date(z.getFullYear(),z.getMonth()+1,0).toISOString().slice(0,10),x=await api('/analytics/departments/'+encodeURIComponent(d)+'/trend?from='+from+'&to='+to);$('trend').innerHTML=`<div class="table"><table><tr><th>Date</th><th>Day</th><th>Headcount</th><th>Present</th><th>Rate</th><th>7-day avg</th></tr>${x.items.map(r=>`<tr><td>${r.date}</td><td>${r.is_working_day?'Working day':'Weekend'}</td><td>${r.headcount}</td><td>${r.present_count}</td><td>${r.attendance_rate==null?'—':(r.attendance_rate*100).toFixed(2)+'%'}</td><td>${r.moving_avg_7d==null?'—':(r.moving_avg_7d*100).toFixed(2)+'%'}</td></tr>`).join('')}</table></div>`}catch(e){err(e)}}
function openModal(){$('modal').classList.add('show')}function closeModal(){$('modal').classList.remove('show')}
async function createEmployee(){let b={emp_code:$('ncode').value.trim(),name:$('nname').value.trim(),email:$('nemail').value.trim(),department:$('ndept').value.trim(),shift_start:$('nstart').value,shift_end:$('nend').value,joined_on:$('njoined').value};try{await api('/employees',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});closeModal();employees();overview()}catch(e){err(e)}}
async function action(){let inA=$('atype').value==='in',b={emp_code:$('aemp').value.trim()};if(inA)b.status=$('astatus').value;try{let r=await api(inA?'/attendance/punch-in':'/attendance/punch-out',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});$('result').textContent=`Success: ${r.emp_code} recorded for ${r.date}.`;overview()}catch(e){$('result').textContent='Error: '+e.message}}
overview();
</script></body></html>""")
