from flask import Flask, render_template, request, jsonify
from dpr_protocol import DPRSession

app = Flask(__name__)

session = None


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

    if not session:
        return jsonify({"error": "no session"})

    result = session.step()

    return jsonify(result)


@app.route("/pause", methods=["POST"])
def pause():
    session.pause()
    return jsonify({"status": "paused"})


@app.route("/resume", methods=["POST"])
def resume():
    session.resume()
    return jsonify({"status": "resumed"})


@app.route("/inject", methods=["POST"])
def inject():

    msg = request.json["message"]

    result = session.inject(msg)

    return jsonify({"status": "ok", "log": result})


if __name__ == "__main__":
    app.run(debug=True)