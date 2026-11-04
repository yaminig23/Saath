"""Saath AI brain - web application entry point.

Milestones 0-10 plus the web app: user accounts, login sessions, the
deterministic task kernel, the Central Assistant, the Daily Life
manager, specialists, memory layers with consent, gated tools, the
background worker, and the browser UI served at / (the web/ folder).
"""

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

import auth
import central
import demo_bookings
import gcal
import gmail
import kernel
import maps
import memory
import organiser
import outcomes
import providers
import reminders
import threads
import tools
import tutor
import video
import worker
from config import settings
from db import get_session, init_db
from model_client import ModelError


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    worker.start_if_enabled()
    yield
    worker.stop()


app = FastAPI(title="Saath Brain", version="0.1.0", lifespan=lifespan)

_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


@app.get("/", include_in_schema=False)
def root():
    """Serve the Saath web app."""
    return FileResponse(os.path.join(_WEB_DIR, "index.html"))


app.mount("/web", StaticFiles(directory=_WEB_DIR), name="web")


# --- request shapes (FastAPI checks these automatically) ---


class RegisterBody(BaseModel):
    username: str
    password: str


class LoginBody(BaseModel):
    username: str
    password: str


class CreateTaskBody(BaseModel):
    objective: str
    department: str = ""
    agent: str = ""
    priority: str = "normal"
    dependencies: list = []
    deadline: str = ""


class TransitionBody(BaseModel):
    to_status: str
    note: str = ""
    result: str = ""
    evidence: str = ""


class ChatBody(BaseModel):
    message: str
    thread_id: int | None = None


class ReminderBody(BaseModel):
    message: str
    deadline: str
    priority: str = "normal"


class MemoryBody(BaseModel):
    layer: str
    category: str
    key: str
    value: str
    department: str = ""
    agent: str = ""
    source_task_id: int | None = None
    expires_at: str = ""


class CorrectMemoryBody(BaseModel):
    value: str


class ConsentBody(BaseModel):
    category: str
    granted: bool


class ToolBody(BaseModel):
    tool: str
    args: dict = {}


class DecideBody(BaseModel):
    approve: bool
    note: str = ""


class TutorPlanBody(BaseModel):
    topic: str
    context: str = ""
    image_data_url: str = ""
    language: str = "en"


class TutorIllustrateBody(BaseModel):
    task_id: int
    prompt: str


class TutorLookBody(BaseModel):
    image_data_url: str
    question: str = ""


class ThreadBody(BaseModel):
    title: str = ""


class OutcomePlanBody(BaseModel):
    goal: str
    context: str = ""
    place: str = ""


class OutcomeReviseBody(BaseModel):
    change: str


class OutcomeFollowupBody(BaseModel):
    when: str
    note: str


class DemoCancelBody(BaseModel):
    booking_task_id: int


class DemoRsvpBody(BaseModel):
    booking_task_id: int


class VideoCreateBody(BaseModel):
    topic: str
    format: str = "short"
    duration_s: int = 10
    context: str = ""
    image_data_url: str = ""
    provider: str = "runway"
    orientation: str = "landscape"
    language: str = "en"


class VideoConfirmBody(BaseModel):
    confirm: bool


class VideoAskBody(BaseModel):
    question: str
    scene_index: int | None = None


def require_user(authorization: str = Header(default=""), db: DbSession = Depends(get_session)):
    """Dependency: the logged-in user, or HTTP 401."""
    user = auth.user_from_header(db, authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or expired login")
    return user


@app.get("/health")
def health() -> dict:
    """Health check proving the service is up."""
    return {"status": "ok", "app": "saath-brain", "env": settings.app_env}


@app.post("/auth/register", status_code=201)
def register(body: RegisterBody, db: DbSession = Depends(get_session)):
    """Create a user account."""
    try:
        user = auth.register_user(db, body.username, body.password)
    except auth.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": user.id, "username": user.username}


@app.post("/auth/login")
def login(body: LoginBody, db: DbSession = Depends(get_session)):
    """Check credentials and issue a login token."""
    try:
        token, expires_at = auth.login_user(db, body.username, body.password)
    except auth.LockedOutError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except auth.AuthError:
        raise HTTPException(status_code=401, detail="wrong username or password")
    return {"token": token, "expires_at": expires_at}


@app.post("/auth/logout")
def logout(authorization: str = Header(default=""), db: DbSession = Depends(get_session)):
    """Throw away the login token used for this request."""
    auth.logout_token(db, authorization)
    return {"ok": True}


@app.post("/tasks", status_code=201)
def create_task(body: CreateTaskBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Create a task in PENDING state."""
    try:
        task = kernel.create_task(
            db,
            user_id=user.id,
            objective=body.objective,
            department=body.department,
            agent=body.agent,
            priority=body.priority,
            dependencies=body.dependencies,
            deadline=body.deadline,
        )
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return task.to_dict()


@app.get("/tasks")
def list_tasks(user=Depends(require_user), db: DbSession = Depends(get_session), active_only: bool = False):
    """List my tasks, oldest first (active_only skips COMPLETED and FAILED)."""
    rows = kernel.list_tasks(db, user.id)
    if active_only:
        rows = [task for task in rows if task.status not in ("COMPLETED", "FAILED")]
    return [task.to_dict() for task in rows]


@app.get("/tasks/{task_id}")
def get_task(task_id: int, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Fetch one task together with its audit trail."""
    try:
        task = kernel.get_task(db, user.id, task_id)
        events = kernel.list_events(db, user.id, task_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="task not found")
    return {"task": task.to_dict(), "events": [event.to_dict() for event in events]}


@app.post("/chat")
def chat(body: ChatBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Talk to the Central Assistant. Each turn is stored as an audited task; with thread_id it joins that thread."""
    try:
        return central.chat(db, user_id=user.id, message=body.message, thread_id=body.thread_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="thread not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/reminders", status_code=201)
def create_reminder(body: ReminderBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Set a reminder due at a specific time (ISO-8601 UTC)."""
    try:
        return reminders.create_reminder(
            db, user_id=user.id, message=body.message, deadline=body.deadline, priority=body.priority
        )
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/reminders/due")
def due_reminders(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """List my reminders whose time has come, oldest first."""
    return reminders.list_due(db, user_id=user.id)


@app.post("/briefing")
def briefing(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Ask the Daily Organiser for today's briefing."""
    return organiser.daily_briefing(db, user_id=user.id)


@app.post("/memories", status_code=201)
def create_memory(body: MemoryBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Store one memory in a layer (sensitive categories need consent)."""
    try:
        return memory.store_memory(
            db,
            user_id=user.id,
            layer=body.layer,
            category=body.category,
            key=body.key,
            value=body.value,
            department=body.department,
            agent=body.agent,
            source_task_id=body.source_task_id,
            expires_at=body.expires_at,
        )
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="source task not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/memories")
def list_memories(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """View all my stored memories, oldest first."""
    return memory.list_memories(db, user_id=user.id)


@app.put("/memories/{memory_id}")
def correct_memory(memory_id: int, body: CorrectMemoryBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Correct one memory's value."""
    try:
        return memory.correct_memory(db, user_id=user.id, memory_id=memory_id, value=body.value)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="memory not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/memories/{memory_id}")
def delete_memory(memory_id: int, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Delete one memory (propagates across layers by key)."""
    try:
        return memory.delete_memory(db, user_id=user.id, memory_id=memory_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="memory not found")


@app.post("/consents")
def set_consent(body: ConsentBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Grant or revoke consent for one category."""
    try:
        return memory.set_consent(db, user_id=user.id, category=body.category, granted=body.granted)
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/consents")
def list_consents(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """List my recorded consent choices."""
    return memory.list_consents(db, user_id=user.id)


@app.post("/tasks/{task_id}/tools")
def call_tool(task_id: int, body: ToolBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Run a tool for an IN_PROGRESS task (consequential tools pause for approval)."""
    try:
        return tools.request_tool(db, user_id=user.id, task_id=task_id, tool_name=body.tool, args=body.args)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="task not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/approvals")
def list_approvals(user=Depends(require_user), db: DbSession = Depends(get_session), status: str = ""):
    """List my approval requests, oldest first (optionally filtered by status)."""
    try:
        return tools.list_approvals(db, user_id=user.id, status=status)
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/approvals/{approval_id}/decide")
def decide_approval(approval_id: int, body: DecideBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Approve or deny a pending approval, exactly once."""
    try:
        return tools.decide_approval(db, user_id=user.id, approval_id=approval_id, approve=body.approve, note=body.note)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="approval not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/tutor/plan")
def tutor_plan(body: TutorPlanBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Teach a topic in steps, planned by the model and stored as an audited task."""
    try:
        return tutor.plan_lesson(
            db, user_id=user.id, topic=body.topic, context=body.context, image_data_url=body.image_data_url,
            language=body.language,
        )
    except ModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/tutor/illustrate")
def tutor_illustrate(body: TutorIllustrateBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Generate one lesson illustration (explicit request: spends provider quota)."""
    try:
        return tutor.illustrate(db, user_id=user.id, task_id=body.task_id, prompt=body.prompt)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="task not found")
    except ModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/tutor/look")
def tutor_look(body: TutorLookBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Answer one question about the user's photo."""
    try:
        return tutor.look_at_photo(db, user_id=user.id, image_data_url=body.image_data_url, question=body.question)
    except ModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/threads", status_code=201)
def create_thread(body: ThreadBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Start a conversation thread for multi-turn context."""
    try:
        return threads.create_thread(db, user_id=user.id, title=body.title)
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/threads")
def list_threads(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """My threads, most recently active first."""
    return threads.list_threads(db, user_id=user.id)


@app.get("/threads/{thread_id}")
def get_thread(thread_id: int, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """One thread with its recent messages."""
    try:
        return threads.get_thread(db, user_id=user.id, thread_id=thread_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="thread not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/outcomes/plan")
def plan_outcome(body: OutcomePlanBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Turn a goal into an owned outcome: open parent, real child steps, recorded path."""
    try:
        return outcomes.plan_goal(db, user_id=user.id, goal=body.goal, context=body.context, place=body.place)
    except ModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/outcomes/{outcome_id}/revise")
def revise_outcome(outcome_id: int, body: OutcomeReviseBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Revise an open outcome: keep and add steps, supersede the rest honestly."""
    try:
        return outcomes.revise_outcome(db, user_id=user.id, outcome_id=outcome_id, change=body.change)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="outcome not found")
    except ModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/outcomes/{outcome_id}/followup")
def outcome_followup(outcome_id: int, body: OutcomeFollowupBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Attach a real reminder follow-up to an outcome."""
    try:
        return outcomes.schedule_followup(db, user_id=user.id, outcome_id=outcome_id, when=body.when, note=body.note)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="outcome not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/providers")
def list_providers(user=Depends(require_user)):
    """Every integration adapter with its honest status. No network calls."""
    return providers.list_providers()


@app.post("/providers/{name}/check")
def check_provider(name: str, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Run one adapter's explicit live check (spends quota only when stated)."""
    if name == "calendar":
        return {"name": name, **gcal.check(db, user_id=user.id)}
    if name == "gmail":
        return {"name": name, **gmail.check(db, user_id=user.id)}
    provider = providers.get_provider(name)
    if provider is None:
        raise HTTPException(status_code=404, detail="unknown provider")
    result = provider.check()
    return {"name": name, **result}


class CalendarAuthUrlBody(BaseModel):
    redirect_uri: str = ""


@app.get("/calendar/status")
def calendar_status(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """My Google Calendar link state. No network calls."""
    return gcal.status(db, user_id=user.id)


@app.post("/calendar/auth-url")
def calendar_auth_url(body: CalendarAuthUrlBody, user=Depends(require_user)):
    """Start connecting: returns the Google approval URL for me."""
    try:
        return gcal.auth_url(user_id=user.id, redirect_uri=body.redirect_uri)
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/calendar/callback")
def calendar_callback(state: str = "", code: str = "", db: DbSession = Depends(get_session)):
    """Google's loopback landing: exchanges the code, shows a plain page.
    No login token here (Google redirects the browser keyless) — the
    one-time state binds the code to the user who asked."""
    from fastapi.responses import HTMLResponse

    try:
        result = gcal.connect_with_code(db, state=state, code=code)
    except kernel.KernelError as exc:
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;padding:2em'><h2>Saath could not connect</h2>"
            f"<p>{exc}</p><p>Close this tab and press Connect again in Saath.</p></body></html>",
            status_code=400,
        )
    who = f" ({result['email']})" if result["email"] else ""
    return HTMLResponse(
        f"<html><body style='font-family:sans-serif;padding:2em'><h2>Saath is connected{who}</h2>"
        f"<p>Close this tab and return to Saath.</p></body></html>"
    )


@app.post("/calendar/disconnect")
def calendar_disconnect(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Forget my Google Calendar link (my Google events stay put)."""
    return gcal.disconnect(db, user_id=user.id)


@app.get("/calendar/day")
def calendar_day(date: str = "", time_zone: str = "UTC", user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Read one day's real events from my Google Calendar."""
    try:
        return gcal.list_day(db, user_id=user.id, date=date, time_zone=time_zone)
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class GmailAuthUrlBody(BaseModel):
    redirect_uri: str = ""


@app.get("/gmail/status")
def gmail_status(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """My Gmail link state. No network calls."""
    return gmail.status(db, user_id=user.id)


@app.post("/gmail/auth-url")
def gmail_auth_url(body: GmailAuthUrlBody, user=Depends(require_user)):
    """Start connecting: returns the Google approval URL for me."""
    try:
        return gmail.auth_url(user_id=user.id, redirect_uri=body.redirect_uri)
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/gmail/callback")
def gmail_callback(state: str = "", code: str = "", db: DbSession = Depends(get_session)):
    """Google's loopback landing: exchanges the code, shows a plain page.
    The one-time state binds the code to the user who asked."""
    from fastapi.responses import HTMLResponse

    try:
        result = gmail.connect_with_code(db, state=state, code=code)
    except kernel.KernelError as exc:
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;padding:2em'><h2>Saath could not connect</h2>"
            f"<p>{exc}</p><p>Close this tab and press Connect again in Saath.</p></body></html>",
            status_code=400,
        )
    who = f" ({result['email']})" if result["email"] else ""
    return HTMLResponse(
        f"<html><body style='font-family:sans-serif;padding:2em'><h2>Saath is connected{who}</h2>"
        f"<p>Close this tab and return to Saath.</p></body></html>"
    )


@app.post("/gmail/disconnect")
def gmail_disconnect(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Forget my Gmail link (sent mail stays sent)."""
    return gmail.disconnect(db, user_id=user.id)


@app.get("/maps/places")
def maps_places(q: str = "", near: str = "", user=Depends(require_user)):
    """Search real places on Google Maps (each call bills a fraction of a cent)."""
    try:
        return maps.search_places(query=q, near=near)
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/maps/route")
def maps_route(from_place: str = "", to_place: str = "", mode: str = "driving", user=Depends(require_user)):
    """Real distance and duration between two places (each call bills a fraction of a cent)."""
    try:
        return maps.route(origin=from_place, destination=to_place, mode=mode)
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/demo/options")
def demo_options(
    kind: str = "cab",
    from_place: str = "",
    to_place: str = "",
    service: str = "",
    date: str = "",
    area: str = "",
    user=Depends(require_user),
):
    """Search the labeled demo catalog (deterministic, reserves nothing)."""
    try:
        if kind == "cab":
            return demo_bookings.search_cabs(from_place, to_place)
        if kind == "appointment":
            return demo_bookings.search_appointments(service, date)
        if kind == "service":
            return demo_bookings.search_services(service, area)
        if kind == "venue":
            return demo_bookings.search_venues(area)
        raise demo_bookings.DemoError("kind must be cab, appointment, service, or venue")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/demo/bookings")
def demo_bookings_list(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """My demo bookings, newest first."""
    return demo_bookings.list_bookings(db, user_id=user.id)


@app.post("/demo/cancel")
def demo_cancel(body: DemoCancelBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Cancel a demo booking through a real cancellation task."""
    try:
        return demo_bookings.cancel_booking(db, user_id=user.id, booking_task_id=body.booking_task_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="demo booking not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/demo/contacts")
def demo_contacts(user=Depends(require_user)):
    """Fictional contacts for demo party invites. No real details exist."""
    return demo_bookings.list_contacts()


@app.post("/demo/party/sample-rsvps")
def demo_sample_rsvps(body: DemoRsvpBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Fill in explicitly-requested sample RSVPs (labeled, deterministic)."""
    try:
        return demo_bookings.sample_rsvps(db, user_id=user.id, booking_task_id=body.booking_task_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="demo party not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/video/jobs", status_code=201)
def create_video_job(body: VideoCreateBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Start an AI video lesson job (refuses honestly without a provider key)."""
    try:
        return video.create_job(
            db,
            user_id=user.id,
            topic=body.topic,
            format=body.format,
            duration_s=body.duration_s,
            context=body.context,
            image_data_url=body.image_data_url,
            provider=body.provider,
            orientation=body.orientation,
            language=body.language,
        )
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/video/jobs")
def list_video_jobs(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """The user's video jobs, newest first."""
    return video.list_jobs(db, user_id=user.id)


@app.get("/video/jobs/{job_id}")
def get_video_job(job_id: int, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """One video job: status, stages, scenes, error when failed."""
    try:
        job = video._get_job(db, user.id, job_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="video job not found")
    return video.job_dict(job)


@app.post("/video/jobs/{job_id}/confirm")
def confirm_video_job(
    job_id: int, body: VideoConfirmBody, user=Depends(require_user), db: DbSession = Depends(get_session)
):
    """Answer the cost gate: true plans the lesson, false cancels the job."""
    try:
        return video.confirm_job(db, user_id=user.id, job_id=job_id, confirm=body.confirm)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="video job not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/video/jobs/{job_id}/cancel")
def cancel_video_job(job_id: int, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Cancel an unfinished video job."""
    try:
        return video.cancel_job(db, user_id=user.id, job_id=job_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="video job not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/video/jobs/{job_id}")
def delete_video_job(job_id: int, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Delete a finished video job and its files."""
    try:
        return video.delete_job(db, user_id=user.id, job_id=job_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="video job not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/video/jobs/{job_id}/file")
def download_video_job(job_id: int, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Stream a finished lesson MP4 (owner only, never public)."""
    try:
        path = video.file_path(db, user_id=user.id, job_id=job_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="video job not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return FileResponse(path, media_type="video/mp4", filename=f"saath-lesson-{job_id}.mp4")


@app.get("/video/jobs/{job_id}/captions")
def read_video_captions(job_id: int, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Captions + transcript of a finished lesson (owner only)."""
    try:
        return video.captions_data(db, user_id=user.id, job_id=job_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="video job not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/video/jobs/{job_id}/ask")
def ask_video_lesson(
    job_id: int, body: VideoAskBody, user=Depends(require_user), db: DbSession = Depends(get_session)
):
    """Ask a follow-up question about a planned video lesson."""
    try:
        return video.ask_about_lesson(db, user_id=user.id, job_id=job_id, question=body.question, scene_index=body.scene_index)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="video job not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/video/jobs/{job_id}/keyframes")
def read_video_keyframes(job_id: int, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Scene keyframes of a video job (owner only)."""
    try:
        return video.keyframes_data(db, user_id=user.id, job_id=job_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="video job not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/failures")
def list_failures(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """List my failure reports, oldest first."""
    return tools.list_failures(db, user_id=user.id)


@app.get("/worker/status")
def worker_status(user=Depends(require_user)):
    """Worker liveness plus the last pass report (running means the thread is alive)."""
    return worker.status()


@app.post("/worker/tick")
def worker_tick(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Run one worker pass now (same code the scheduler loop calls)."""
    return worker.manual_tick(db)


@app.get("/notifications")
def list_notifications(user=Depends(require_user), db: DbSession = Depends(get_session)):
    """List my notifications, oldest first."""
    return worker.list_notifications(db, user_id=user.id)


@app.post("/notifications/{notification_id}/read")
def read_notification(notification_id: int, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Mark one notification read."""
    try:
        return worker.mark_read(db, user_id=user.id, notification_id=notification_id)
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="notification not found")


@app.post("/tasks/{task_id}/transition")
def transition_task(task_id: int, body: TransitionBody, user=Depends(require_user), db: DbSession = Depends(get_session)):
    """Move a task to a new status if the kernel allows it."""
    try:
        task = kernel.transition_task(
            db,
            user_id=user.id,
            task_id=task_id,
            to_status=body.to_status,
            note=body.note,
            result=body.result,
            evidence=body.evidence,
        )
    except kernel.TaskNotFoundError:
        raise HTTPException(status_code=404, detail="task not found")
    except kernel.KernelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return task.to_dict()
