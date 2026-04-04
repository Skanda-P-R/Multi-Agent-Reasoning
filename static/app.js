let running = false
let paused = false

let commandType = null

const chat = document.getElementById("chat")

function scrollBottom() {
    chat.scrollTop = chat.scrollHeight
}

function addMessage(role, text) {
    const div = document.createElement("div")

    div.classList.add("message")
    div.classList.add(role)

    div.innerHTML = `
    <div class="bubble">
        ${marked.parse(text)}
    </div>
`

    chat.appendChild(div)
    scrollBottom()
}

function addSystem(text) {
    addMessage("system", text)
}

function showHumanTurnOptions() {
    document.getElementById("human-turn-options").classList.remove("hidden")
}

function hideHumanTurnOptions() {
    document.getElementById("human-turn-options").classList.add("hidden")
}

function showCommandBox(type, label, placeholder) {
    commandType = type
    document.getElementById("command-label").textContent = label

    const input = document.getElementById("command-input")
    input.placeholder = placeholder

    const redirectTurns = document.getElementById("redirect-turns")
    if (type === "redirect" || type === "human_turn_redirect") {
        redirectTurns.classList.remove("hidden")
    } else {
        redirectTurns.classList.add("hidden")
    }

    document.getElementById("command-box").classList.remove("hidden")
    input.focus()
}

async function start() {
    const q = document.getElementById("question").value.trim()
    if (!q) return

    chat.innerHTML = ""
    addMessage("human", q)

    await fetch("/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q })
    })

    running = true
    paused = false
    hideHumanTurnOptions()

    loop()
}

async function loop() {
    if (!running || paused) return

    const res = await fetch("/step", { method: "POST" })
    const data = await res.json()

    if (data.status === "error") {
        addSystem(`Error: ${data.error || "Unknown server error"}`)
        running = false
        return
    }

    if (data.status === "paused") {
        paused = true
        return
    }

    if (data.status === "awaiting_human_turn") {
        paused = true
        addSystem("Human turn selected. Choose Inject or Redirect.")
        showHumanTurnOptions()
        return
    }

    if (data.status === "done") {
        addMessage("system", `**${data.agent}**\n\n${data.text}`)
        running = false
        hideHumanTurnOptions()
        return
    }

    const agentClass = data.agent.replace(" ", "").toLowerCase()

    let meta = ""
    if (data.ignored) {
        meta += `\n\n_Ignored response_: ${data.ignored_reason}`
    }
    if (typeof data.quota_left !== "undefined" && data.quota_left !== null) {
        meta += `\n\n_Quota left_: ${data.quota_left}`
    }
    if (Array.isArray(data.queued_interrupts) && data.queued_interrupts.length) {
        meta += `\n\n_Hand queue_: ${data.queued_interrupts.join(", ")}`
    }

    addMessage(agentClass, `**${data.agent}**\n\n${data.text}${meta}`)

    setTimeout(loop, 900)
}

async function pause() {
    await fetch("/pause", { method: "POST" })

    paused = true
    addMessage("system", "Session paused")
}

async function resume() {
    await fetch("/resume", { method: "POST" })

    paused = false
    addMessage("system", "Session resumed")

    loop()
}

async function raiseHand() {
    const res = await fetch("/raise_hand", { method: "POST" })
    const data = await res.json()

    if (data.status === "error") {
        addSystem(`Error: ${data.error}`)
        return
    }

    addMessage("human", "Hand raised for protocol turn.")
}

function showInject() {
    hideHumanTurnOptions()
    showCommandBox("inject", "INJECT", "Enter human instruction...")
}

function showRedirect() {
    hideHumanTurnOptions()
    showCommandBox("redirect", "REDIRECT", "Enter redirection objective...")
}

function showHumanTurnInject() {
    showCommandBox("human_turn_inject", "HUMAN TURN: INJECT", "Enter instruction for agents...")
}

function showHumanTurnRedirect() {
    showCommandBox("human_turn_redirect", "HUMAN TURN: REDIRECT", "Enter redirection objective...")
}

async function submitCommand() {
    const input = document.getElementById("command-input")
    const msg = input.value.trim()

    if (!msg) return

    if (commandType === "inject") {
        await fetch("/inject", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: msg })
        })

        addMessage("human", `Inject: ${msg}`)
    }

    if (commandType === "redirect") {
        const turnsRaw = document.getElementById("redirect-turns").value
        const turns = Math.max(1, parseInt(turnsRaw || "3", 10))

        await fetch("/redirect", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: msg, turns })
        })

        addMessage("human", `Redirect (${turns} turns): ${msg}`)
    }

    if (commandType === "human_turn_inject") {
        const res = await fetch("/human_turn", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "inject", message: msg })
        })
        const data = await res.json()

        if (data.status === "error") {
            addSystem(`Error: ${data.error}`)
            return
        }

        addMessage("human", `Inject: ${msg}`)
        hideHumanTurnOptions()
        paused = false
        setTimeout(loop, 300)
    }

    if (commandType === "human_turn_redirect") {
        const turnsRaw = document.getElementById("redirect-turns").value
        const turns = Math.max(1, parseInt(turnsRaw || "3", 10))

        const res = await fetch("/human_turn", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "redirect", message: msg, turns })
        })
        const data = await res.json()

        if (data.status === "error") {
            addSystem(`Error: ${data.error}`)
            return
        }

        addMessage("human", `Redirect (${turns} turns): ${msg}`)
        hideHumanTurnOptions()
        paused = false
        setTimeout(loop, 300)
    }

    input.value = ""
    document.getElementById("command-box").classList.add("hidden")
    commandType = null
}
