from flask import Flask, render_template, request, jsonify
from dpr_history import (
    list_history_summaries,
    load_history_document,
    save_history_document,
)
from dpr_protocol import (
    DPRSession,
    AVAILABLE_MODELS,
    DEFAULT_AGENT_MODELS,
    DEFAULT_SECTION,
    SECTION_HEADERS,
    suggest_models_for_question,
)

# --------------------------------------------
# APP SETUP
# --------------------------------------------
app = Flask(__name__)

session = None


# --------------------------------------------
# INTERNAL HELPERS
# --------------------------------------------
def _require_session():
    if not session:
        return jsonify({"status": "error", "error": "no session"}), 400
    return None


@app.route("/")
def index():
    return render_template("index.html")


# --------------------------------------------
# SESSION LIFECYCLE ROUTES
# --------------------------------------------
@app.route("/start", methods=["POST"])
def start():

    global session

    payload = request.json or {}
    question = (payload.get("question") or "").strip()
    models = payload.get("models") or []

    if not question:
        return jsonify({"status": "error", "error": "question is required"}), 400

    try:
        session = DPRSession(question, selected_models=models)
    except ValueError as e:
        return jsonify({"status": "error", "error": str(e)}), 400

    return jsonify({
        "status": "started",
        "agents": session.agents
    })


@app.route("/models", methods=["GET"])
def models():
    default_models = [{"model": m, "section": DEFAULT_SECTION} for m in DEFAULT_AGENT_MODELS]
    return jsonify({
        "models": AVAILABLE_MODELS,
        "default_models": default_models,
        "sections": list(SECTION_HEADERS.keys()),
        "default_section": DEFAULT_SECTION,
    })


@app.route("/suggest_models", methods=["POST"])
def suggest_models():
    payload = request.json or {}
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"status": "error", "error": "question is required"}), 400

    suggestion = suggest_models_for_question(question)
    return jsonify({
        "status": "ok",
        "suggestion": suggestion,
    })


@app.route("/step", methods=["POST"])
def step():

    global session

    missing = _require_session()
    if missing:
        return missing

    try:
        result = session.step()
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

    session.record_event("step_result", result)
    return jsonify(result)


@app.route("/pause", methods=["POST"])
def pause():
    missing = _require_session()
    if missing:
        return missing

    session.pause()
    session.record_event("pause", {"status": "paused"})
    return jsonify({"status": "paused"})


@app.route("/resume", methods=["POST"])
def resume():
    missing = _require_session()
    if missing:
        return missing

    session.resume()
    session.record_event("resume", {"status": "resumed"})
    return jsonify({"status": "resumed"})


@app.route("/set_models", methods=["POST"])
def set_models():
    missing = _require_session()
    if missing:
        return missing

    models = (request.json or {}).get("models") or []
    try:
        agents = session.update_agents(models)
    except (RuntimeError, ValueError) as e:
        return jsonify({"status": "error", "error": str(e)}), 400

    return jsonify({"status": "ok", "agents": agents})


@app.route("/stop", methods=["POST"])
def stop():
    missing = _require_session()
    if missing:
        return missing

    session.stop()
    result = {
        "status": "done",
        "agent": "Human",
        "text": "Reasoning stopped by human.",
        **session._state_payload()
    }
    session.record_event("stop", result)
    return jsonify(result)


# --------------------------------------------
# HUMAN INTERVENTION ROUTES
# --------------------------------------------
@app.route("/inject", methods=["POST"])
def inject():
    missing = _require_session()
    if missing:
        return missing

    msg = request.json.get("message", "").strip()
    if not msg:
        return jsonify({"status": "error", "error": "message is required"}), 400

    result = session.inject(msg)
    session.record_event("inject", {"message": msg, "result": result})

    return jsonify({"status": "ok", "log": result})


@app.route("/raise_hand", methods=["POST"])
def raise_hand():
    missing = _require_session()
    if missing:
        return missing

    result = session.raise_human_hand()
    session.record_event("raise_hand", result)
    return jsonify({"status": "ok", "log": result})


@app.route("/human_turn", methods=["POST"])
def human_turn():
    missing = _require_session()
    if missing:
        return missing

    action = request.json.get("action")
    msg = request.json.get("message", "").strip()
    if not msg:
        return jsonify({"status": "error", "error": "message is required"}), 400

    turns = request.json.get("turns", 3)

    try:
        if action in ("inject", "redirect"):
            result = session.submit_human_turn_action(action, msg, turns)
        else:
            result = session.submit_human_turn(msg)
    except RuntimeError as e:
        return jsonify({"status": "error", "error": str(e)}), 400

    session.record_event("human_turn", {"action": action or "turn", "message": msg, "turns": turns, "result": result})
    return jsonify(result)


@app.route("/redirect", methods=["POST"])
def redirect():
    missing = _require_session()
    if missing:
        return missing

    msg = request.json.get("message", "").strip()
    turns = request.json.get("turns", 3)

    if not msg:
        return jsonify({"status": "error", "error": "message is required"}), 400

    result = session.redirect(msg, turns)
    session.record_event("redirect", {"message": msg, "turns": turns, "result": result})
    return jsonify({"status": "ok", "log": result})


# --------------------------------------------
# FINALIZATION ROUTES
# --------------------------------------------
@app.route("/finalize/approve", methods=["POST"])
def finalize_approve():
    missing = _require_session()
    if missing:
        return missing

    try:
        result = session.approve_finalization()
    except RuntimeError as e:
        return jsonify({"status": "error", "error": str(e)}), 400

    session.record_event("finalize_approve", result)
    return jsonify(result)


@app.route("/finalize/continue", methods=["POST"])
def finalize_continue():
    missing = _require_session()
    if missing:
        return missing

    redirect_message = request.json.get("redirect_message", "").strip()
    turns = request.json.get("turns", 3)

    try:
        result = session.continue_after_finalization(
            redirect_message=redirect_message or None,
            turns=turns
        )
    except RuntimeError as e:
        return jsonify({"status": "error", "error": str(e)}), 400

    session.record_event("finalize_continue", {"redirect_message": redirect_message, "turns": turns, "result": result})
    return jsonify(result)


# --------------------------------------------
# HISTORY ROUTES
# --------------------------------------------
@app.route("/history", methods=["GET"])
def history_list():
    return jsonify({
        "status": "ok",
        "items": list_history_summaries(),
    })


@app.route("/history/save_current", methods=["POST"])
def history_save_current():
    global session

    missing = _require_session()
    if missing:
        return missing

    if not session.ended:
        return jsonify({"status": "error", "error": "session has not ended"}), 400

    document = save_history_document(session.to_history_document())
    saved_id = document["id"]
    session = None
    return jsonify({
        "status": "ok",
        "id": saved_id,
        "saved_at": document.get("saved_at"),
    })


@app.route("/history/<history_id>", methods=["GET"])
def history_get(history_id):
    try:
        document = load_history_document(history_id)
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"status": "error", "error": str(e)}), 404

    return jsonify({"status": "ok", "history": document})


@app.route("/history/<history_id>/load", methods=["POST"])
def history_load(history_id):
    global session

    try:
        document = load_history_document(history_id)
        session = DPRSession.from_history_document(document, history_id=history_id)
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"status": "error", "error": str(e)}), 404

    return jsonify({
        "status": "ok",
        "history_id": history_id,
        "session": session.restore_payload(),
    })


@app.route("/history/continue", methods=["POST"])
def history_continue():
    missing = _require_session()
    if missing:
        return missing

    payload = request.json or {}
    msg = (payload.get("message") or "").strip()
    turns = payload.get("turns", 3)

    try:
        result = session.continue_from_history_redirect(msg, turns)
    except RuntimeError as e:
        return jsonify({"status": "error", "error": str(e)}), 400

    session.record_event("history_continue", {"message": msg, "turns": turns, "result": result})
    return jsonify(result)


# --------------------------------------------
# DEV ENTRYPOINT
# --------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
