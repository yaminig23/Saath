"""Visual Tutor backend (M9 product).

The tutor teaches in simple numbered steps planned by the model, can
illustrate a step with a Muse Image still, and can look at the user's
photo with Muse Spark vision. Every lesson and photo question is a
real audited kernel task (department "daily-life", agent "tutor").

Honesty rules: the offline stub teaches a generic learning framework
(labeled by its stub model name) and raises for pixels, which are
never faked; illustrations generate only on explicit request, since
each one spends provider quota.
"""

import json

import kernel
import model_client
from model_client import ModelError

TUTOR_DEPARTMENT = "daily-life"
TUTOR_AGENT = "tutor"

MAX_TOPIC_LEN = 500
MAX_CONTEXT_LEN = 2000
MAX_QUESTION_LEN = 1000
MAX_IMAGE_PROMPT_LEN = 1000
# Base64 of roughly 5 MB.
MAX_IMAGE_DATA_URL_LEN = 7000000

LESSON_MAX_OUTPUT_TOKENS = 4000

LESSON_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "image_prompt": {"type": "string"},
                },
                "required": ["title", "text", "image_prompt"],
                "additionalProperties": False,
            },
        },
        "follow_up": {"type": "string"},
    },
    "required": ["steps", "follow_up"],
    "additionalProperties": False,
}

LESSON_FORMAT = {"type": "json_schema", "name": "tutor_lesson", "schema": LESSON_SCHEMA, "strict": True}

LESSON_PROMPT = """You are Saath's Visual Tutor: a patient teacher. Plan a short lesson (3 to 6 steps) for the topic below. Each step needs a short title, plain-language teaching text (2-4 sentences, no jargon), and an image_prompt describing one simple helpful illustration for that step (or "" when no picture would help). Never include unsafe instructions (no electrical rewiring, no medical diagnosis, nothing needing a licensed professional); for those, teach how to find qualified help instead. Reply with the lesson JSON only."""

LOOK_PROMPT = """You are Saath's Visual Tutor. Look at the user's photo and answer the question precisely: describe only what you can actually see, say plainly when the photo is unclear, and teach the next step in simple words. Never guess brand names, model numbers, or text you cannot read."""


class TutorError(kernel.KernelError):
    """A tutor request broke a tutor rule."""


def _client():
    return model_client.get_model_client()


def _validate_lesson(raw) -> tuple:
    """Check a model-produced lesson. Returns (steps, follow_up)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
        raise ValueError("lesson is not a steps object")
    steps = []
    for entry in raw["steps"]:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).strip()
        text = str(entry.get("text", "")).strip()
        image_prompt = str(entry.get("image_prompt", "")).strip()
        if not title or not text:
            continue
        steps.append(
            {"title": title[:200], "text": text[:2000], "image_prompt": image_prompt[:MAX_IMAGE_PROMPT_LEN]}
        )
        if len(steps) >= 8:
            break
    if not steps:
        raise ValueError("lesson has no usable steps")
    follow_up = raw.get("follow_up", "")
    follow_up = str(follow_up).strip()[:500] if isinstance(follow_up, str) else ""
    return steps, follow_up


def _stub_lesson(topic: str) -> list:
    """Honest offline framework (the stub model name marks it downstream)."""
    return [
        {
            "title": "Say the goal in one sentence",
            "text": (
                f"Write down what '{topic}' means for you: what will you be able to do afterwards? "
                "One clear sentence keeps the whole lesson focused."
            ),
            "image_prompt": "",
        },
        {
            "title": "Split it into three tiny pieces",
            "text": (
                "Break the goal into the smallest steps you can practise one at a time. "
                "Do the easiest piece first and say out loud what you did."
            ),
            "image_prompt": "",
        },
        {
            "title": "Practise, then ask for the real lesson",
            "text": (
                "Try one piece today. When the live Muse model is connected, Saath will plan "
                f"proper steps for '{topic}' and illustrate them."
            ),
            "image_prompt": "",
        },
    ]


def _check_image_data_url(value) -> str:
    data_url = (value or "").strip()
    if not data_url:
        return ""
    if not data_url.startswith("data:image/"):
        raise TutorError("photo must be a data:image/... data URL")
    if len(data_url) > MAX_IMAGE_DATA_URL_LEN:
        raise TutorError("photo is too large (about 5 MB max)")
    return data_url


def plan_lesson(db, *, user_id: int, topic: str, context: str = "", image_data_url: str = "", extra_guidance: str = "", language: str = "en") -> dict:
    """Teach `topic` in steps. Returns the audited lesson task plus steps."""
    clean_topic = (topic or "").strip()
    if not clean_topic:
        raise TutorError("topic must not be empty")
    if len(clean_topic) > MAX_TOPIC_LEN:
        raise TutorError("topic must be at most 500 characters")
    clean_context = (context or "").strip()
    if len(clean_context) > MAX_CONTEXT_LEN:
        raise TutorError("context must be at most 2000 characters")
    clean_language = (language or "en").strip().lower()
    if clean_language not in ("en", "hi"):
        raise TutorError("language must be en or hi")
    photo = _check_image_data_url(image_data_url)
    client = _client()
    task = kernel.create_task(
        db,
        user_id=user_id,
        objective=f"Teach me: {clean_topic[:160]}",
        department=TUTOR_DEPARTMENT,
        agent=TUTOR_AGENT,
        priority="normal",
    )
    kernel.transition_task(db, user_id=user_id, task_id=task.id, to_status="IN_PROGRESS", note="lesson started")
    vision_used = False
    image_note = ""
    try:
        if photo and client.supports_vision:
            seen = client.describe_image(
                image_data_url=photo,
                question="Describe this photo precisely so a teacher can plan a lesson around it.",
                system=LOOK_PROMPT,
            )
            clean_context = f"Photo shows: {seen.text}\n\n{clean_context}".strip()
            vision_used = True
        elif photo:
            image_note = "Photo attached, but Saath cannot see photos without the live model — describe it in words for now."
        if not client.supports_planning:
            steps = _stub_lesson(clean_topic)
            follow_up = "Connect the live Muse model for a real step-by-step lesson on this topic."
            model_name = client.name
            evidence = f"model={model_name} steps={len(steps)} stub-lesson"
        else:
            message = f"Topic: {clean_topic}"
            if clean_context:
                message += f"\n\nContext: {clean_context}"
            if (extra_guidance or "").strip():
                message += f"\n\nAlso follow this guidance: {(extra_guidance or '').strip()[:500]}"
            if clean_language == "hi":
                message += "\n\nWrite the lesson in Hindi (Devanagari script): titles, texts, and follow-up."
            try:
                response = client.generate(
                    system=LESSON_PROMPT,
                    messages=[{"role": "user", "content": message}],
                    response_format=LESSON_FORMAT,
                    max_output_tokens=LESSON_MAX_OUTPUT_TOKENS,
                )
                shortened = False
            except ModelError as exc:
                if "max_output_tokens" not in str(exc):
                    raise
                response = client.generate(
                    system=LESSON_PROMPT,
                    messages=[
                        {
                            "role": "user",
                            "content": message + "\n\nKeep it short: at most 3 steps, brief sentences.",
                        }
                    ],
                    response_format=LESSON_FORMAT,
                    max_output_tokens=LESSON_MAX_OUTPUT_TOKENS,
                )
                shortened = True
            try:
                raw = json.loads(response.text)
            except ValueError:
                raise ModelError("tutor did not return JSON")
            steps, follow_up = _validate_lesson(raw)
            model_name = response.model
            evidence = (
                f"model={model_name} steps={len(steps)} vision={vision_used} "
                f"tokens={response.input_tokens}/{response.output_tokens} lang={clean_language}"
                + (" shortened-retry" if shortened else "")
            )
    except Exception as exc:
        kernel.transition_task(
            db,
            user_id=user_id,
            task_id=task.id,
            to_status="FAILED",
            note="lesson planning failed",
            result=f"failed: {exc}",
        )
        raise
    summary = "; ".join(step["title"] for step in steps)
    kernel.transition_task(
        db,
        user_id=user_id,
        task_id=task.id,
        to_status="COMPLETED",
        note=f"lesson planned ({len(steps)} steps)",
        result=summary,
        evidence=evidence,
    )
    return {
        "task_id": task.id,
        "topic": clean_topic,
        "steps": steps,
        "follow_up": follow_up,
        "model": model_name,
        "vision_used": vision_used,
        "image_note": image_note,
    }


def illustrate(db, *, user_id: int, task_id: int, prompt: str) -> dict:
    """Generate one step illustration (explicit request: spends provider quota)."""
    task = kernel.get_task(db, user_id, task_id)
    cleaned = (prompt or "").strip()
    if not cleaned:
        raise TutorError("prompt must not be empty")
    if len(cleaned) > MAX_IMAGE_PROMPT_LEN:
        raise TutorError("prompt must be at most 1000 characters")
    made = _client().generate_image(prompt=cleaned)
    kernel.log_event(
        db, user_id=user_id, task_id=task.id, event_type="tool_call", note=f"illustration by {made['model']}"
    )
    return {
        "image_data_url": f"data:image/{made['format']};base64,{made['b64']}",
        "model": made["model"],
        "task_id": task.id,
    }


def look_at_photo(db, *, user_id: int, image_data_url: str, question: str) -> dict:
    """Answer one question about the user's photo. Fails honestly offline."""
    photo = _check_image_data_url(image_data_url)
    if not photo:
        raise TutorError("a photo is required")
    clean_question = (question or "").strip() or "What is in this photo?"
    if len(clean_question) > MAX_QUESTION_LEN:
        raise TutorError("question must be at most 1000 characters")
    task = kernel.create_task(
        db,
        user_id=user_id,
        objective=f"Explain my photo: {clean_question[:120]}",
        department=TUTOR_DEPARTMENT,
        agent=TUTOR_AGENT,
        priority="normal",
    )
    kernel.transition_task(db, user_id=user_id, task_id=task.id, to_status="IN_PROGRESS", note="looking at photo")
    client = _client()
    if not client.supports_vision:
        kernel.transition_task(
            db,
            user_id=user_id,
            task_id=task.id,
            to_status="FAILED",
            note="vision unavailable",
            result="failed: image understanding needs the live Muse model",
        )
        raise ModelError("image understanding needs the live Muse model (no API key set)")
    try:
        seen = client.describe_image(image_data_url=photo, question=clean_question, system=LOOK_PROMPT)
    except Exception as exc:
        kernel.transition_task(
            db,
            user_id=user_id,
            task_id=task.id,
            to_status="FAILED",
            note="looking failed",
            result=f"failed: {exc}",
        )
        raise
    kernel.transition_task(
        db,
        user_id=user_id,
        task_id=task.id,
        to_status="COMPLETED",
        note="photo explained",
        result=seen.text[:2000],
        evidence=f"model={seen.model} tokens={seen.input_tokens}/{seen.output_tokens}",
    )
    return {"task_id": task.id, "description": seen.text, "model": seen.model}
