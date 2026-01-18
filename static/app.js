let rounds = [];              
let currentRound = 0;        
let activeRound = null;       
let isRunning = false;        
let context = null;
let question = "";

const delay = ms => new Promise(res => setTimeout(res, ms));

function updateSubmitState() {
    const textarea = document.getElementById("question");
    const btn = document.getElementById("submit-btn");

    btn.disabled = textarea.value.trim().length === 0;
}

function showSpinner(el, text) {
    el.innerHTML = `
        <div class="spinner">
            <div class="loader"></div>
            <span class="status-text">${text}</span>
        </div>
    `;
}

async function typeBySentence(el, markdownText, speed = 550) {
    el.innerHTML = "";

    const sentences =
        markdownText.match(/[^.!?]+[.!?]+/g) || [markdownText];

    let acc = "";
    for (const s of sentences) {
        acc += s;
        el.innerHTML = marked.parse(acc);

        el.scrollTop = el.scrollHeight;

        await delay(speed);
    }
}


function updateRoundLabel() {
    document.getElementById("round-label").innerText =
        `Round ${currentRound + 1}`;
}

function start() {
    const input = document.getElementById("question");
    question = input.value.trim();
    if (!question) return;

    rounds = [];
    currentRound = 0;
    activeRound = null;
    context = null;
    question = document.getElementById("question").value.trim();
    isRunning = false;

    document.getElementById("final").classList.add("hidden");
    document.getElementById("final").innerHTML = "";

    runRound();
}

async function runRound() {
    isRunning = true;

    const aBox = document.getElementById("agentA");
    const bBox = document.getElementById("agentB");
    const jBox = document.getElementById("judge");

    aBox.className = "box";
    bBox.className = "box";
    jBox.className = "box full";

    showSpinner(aBox, "Agent A thinking...");
    showSpinner(bBox, "Agent B thinking...");
    jBox.innerHTML = "";

    const res = await fetch("/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, context })
    });

    const data = await res.json();
    activeRound = data;

    await Promise.all([
    typeBySentence(aBox, data.agent_a),
    typeBySentence(bBox, data.agent_b)
    ]);

    await delay(700);
    showSpinner(jBox, "Judge deciding...");

    await delay(1200);
    await typeBySentence(jBox, data.judge);

    await delay(800);
    aBox.className = "box " + (data.agree_a ? "green" : "red");
    bBox.className = "box " + (data.agree_b ? "green" : "red");

    rounds.push(data);
    currentRound = rounds.length - 1;
    context = data.new_context;
    activeRound = null;
    isRunning = false;

    updateRoundLabel();

    if (data.finished) {
        document.getElementById("final").innerHTML =
            "<h3>Final Verdict</h3>" + marked.parse(data.judge);
        document.getElementById("final").classList.remove("hidden");
    } else {
        await delay(1200);
        runRound();
    }
}

function loadRound() {
    const r = rounds[currentRound];
    if (!r) return;

    const a = document.getElementById("agentA");
    const b = document.getElementById("agentB");
    const j = document.getElementById("judge");

    a.className = "box " + (r.agree_a ? "green" : "red");
    b.className = "box " + (r.agree_b ? "green" : "red");

    a.innerHTML = marked.parse(r.agent_a);
    b.innerHTML = marked.parse(r.agent_b);
    j.innerHTML = marked.parse(r.judge);

    updateRoundLabel();
}

function prevRound() {
    if (isRunning) return;
    if (currentRound > 0) {
        currentRound--;
        loadRound();
    }
}

function nextRound() {
    if (isRunning) return;
    if (currentRound < rounds.length - 1) {
        currentRound++;
        loadRound();
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const textarea = document.getElementById("question");
    textarea.addEventListener("input", updateSubmitState);
});
