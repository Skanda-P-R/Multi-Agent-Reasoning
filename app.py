from flask import Flask, render_template, request, jsonify
from dpr_protocol import DPRSession

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

    question = request.json["question"]

    session = DPRSession(question)

    return jsonify({"status": "started"})


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


if __name__ == "__main__":
    app.run(debug=True)