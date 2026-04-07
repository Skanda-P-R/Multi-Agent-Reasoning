from flask import Flask, render_template, request, jsonify
from dpr_protocol import (
    DPRSession,
    AVAILABLE_MODELS,
    DEFAULT_AGENT_MODELS,
    DEFAULT_SECTION,
    SECTION_HEADERS,
    suggest_models_for_question,
)

app = Flask(__name__)

session = None


def _require_session():
    if not session:
        return jsonify({"status": "error", "error": "no session"}), 400
    return None


@app.route("/")
def index():
    return render_template("index.html")


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

    return jsonify(result)


@app.route("/pause", methods=["POST"])
def pause():
    missing = _require_session()
    if missing:
        return missing

    session.pause()
    return jsonify({"status": "paused"})


@app.route("/resume", methods=["POST"])
def resume():
    missing = _require_session()
    if missing:
        return missing

    session.resume()
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
    return jsonify({
        "status": "done",
        "agent": "Human",
        "text": "Reasoning stopped by human."
    })


@app.route("/inject", methods=["POST"])
def inject():
    missing = _require_session()
    if missing:
        return missing

    msg = request.json.get("message", "").strip()
    if not msg:
        return jsonify({"status": "error", "error": "message is required"}), 400

    result = session.inject(msg)

    return jsonify({"status": "ok", "log": result})


@app.route("/raise_hand", methods=["POST"])
def raise_hand():
    missing = _require_session()
    if missing:
        return missing

    result = session.raise_human_hand()
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
    return jsonify({"status": "ok", "log": result})


@app.route("/finalize/approve", methods=["POST"])
def finalize_approve():
    missing = _require_session()
    if missing:
        return missing

    try:
        result = session.approve_finalization()
    except RuntimeError as e:
        return jsonify({"status": "error", "error": str(e)}), 400

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

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True)
