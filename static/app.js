const STEP_DELAY_MS = 900

let running = false
let paused = false
let commandType = null

const chat = document.getElementById("chat")
const memoryDrawer = document.getElementById("memory-drawer")
const memoryView = document.getElementById("memory-view")
const contextView = document.getElementById("context-view")

function scrollBottom() {
    chat.scrollTop = chat.scrollHeight
}

function escapeHtml(str) {
    return (str || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
}

function truncateForDrawer(text, limit = 260) {
    if (!text) return ""
    if (text.length <= limit) return text
    return `${text.slice(0, limit).trim()} [truncated in view]`
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

function toggleMemoryDrawer() {
    memoryDrawer.classList.toggle("open")
}

function renderMemory(memory, contextPreview) {
    if (!memory) return

    const sections = [
        ["facts", "Facts / Assumptions"],
        ["options", "Options / Proposals"],
        ["decisions", "Decisions"],
        ["open_questions", "Open Questions"],
        ["actions", "Action Items"],
        ["changelog", "Changelog"],
    ]

    let html = ""
    for (const [key, title] of sections) {
        const values = Array.isArray(memory[key]) ? memory[key] : []
        const items = values.length
            ? values.map((v) => `<li>${escapeHtml(truncateForDrawer(v))}</li>`).join("")
            : "<li>(none)</li>"

        html += `
        <section class="memory-section">
            <h3>${title}</h3>
            <ul>${items}</ul>
        </section>
        `
    }

    memoryView.innerHTML = html
    contextView.textContent = contextPreview || "(no context yet)"
}

function showHumanTurnOptions() {
    document.getElementById("human-turn-options").classList.remove("hidden")
}

function hideHumanTurnOptions() {
    document.getElementById("human-turn-options").classList.add("hidden")
}

function showFinalizationOptions() {
    document.getElementById("finalization-options").classList.remove("hidden")
}

function hideFinalizationOptions() {
    document.getElementById("finalization-options").classList.add("hidden")
}

function showCommandBox(type, label, placeholder) {
    commandType = type
    document.getElementById("command-label").textContent = label

    const input = document.getElementById("command-input")
    input.placeholder = placeholder

    const redirectTurns = document.getElementById("redirect-turns")
    if (type === "human_turn_redirect") {
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
    memoryView.innerHTML = ""
    contextView.textContent = "(waiting for first turn)"

    await fetch("/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q })
    })

    running = true
    paused = false
    hideHumanTurnOptions()
    hideFinalizationOptions()

    loop()
}

async function loop() {
    if (!running || paused) return

    const res = await fetch("/step", { method: "POST" })
    const data = await res.json()

    renderMemory(data.memory, data.context_preview)

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

    if (data.status === "awaiting_human_finalization") {
        paused = true
        const candidate = data.finalization_candidate || {}
        addSystem(
            `Completion candidate by **${candidate.agent || "Agent"}**. Human approval required.`
        )
        showFinalizationOptions()
        return
    }

    if (data.status === "done") {
        addMessage("system", `**${data.agent}**\n\n${data.text}`)
        running = false
        hideHumanTurnOptions()
        hideFinalizationOptions()
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

    setTimeout(loop, STEP_DELAY_MS)
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

async function stopReasoning() {
    const res = await fetch("/stop", { method: "POST" })
    const data = await res.json()

    if (data.status === "error") {
        addSystem(`Error: ${data.error}`)
        return
    }

    if (data.memory || data.context_preview) {
        renderMemory(data.memory, data.context_preview)
    }

    addMessage("system", `**${data.agent || "Human"}**\n\n${data.text || "Reasoning stopped."}`)
    hideHumanTurnOptions()
    hideFinalizationOptions()
    running = false
    paused = false
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

function showHumanTurnInject() {
    showCommandBox("human_turn_inject", "HUMAN TURN: INJECT", "Enter instruction for agents...")
}

function showHumanTurnRedirect() {
    showCommandBox("human_turn_redirect", "HUMAN TURN: REDIRECT", "Enter redirection objective...")
}

function showFinalizeRedirect() {
    showCommandBox("finalize_redirect", "REDIRECT & CONTINUE", "Enter redirection objective...")
}

async function approveStop() {
    const res = await fetch("/finalize/approve", { method: "POST" })
    const data = await res.json()
    if (data.status === "error") {
        addSystem(`Error: ${data.error}`)
        return
    }

    renderMemory(data.memory, data.context_preview)
    addMessage("system", `**${data.agent}**\n\n${data.text}`)
    hideFinalizationOptions()
    running = false
    paused = false
}

async function continueIteration() {
    const res = await fetch("/finalize/continue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
    })
    const data = await res.json()
    if (data.status === "error") {
        addSystem(`Error: ${data.error}`)
        return
    }

    renderMemory(data.memory, data.context_preview)
    hideFinalizationOptions()
    paused = false
    setTimeout(loop, 300)
}

async function submitCommand() {
    const input = document.getElementById("command-input")
    const msg = input.value.trim()

    if (!msg) return

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

        renderMemory(data.memory, data.context_preview)
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

        renderMemory(data.memory, data.context_preview)
        addMessage("human", `Redirect (${turns} turns): ${msg}`)
        hideHumanTurnOptions()
        paused = false
        setTimeout(loop, 300)
    }

    if (commandType === "finalize_redirect") {
        const turnsRaw = document.getElementById("redirect-turns").value
        const turns = Math.max(1, parseInt(turnsRaw || "3", 10))

        const res = await fetch("/finalize/continue", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ redirect_message: msg, turns })
        })
        const data = await res.json()

        if (data.status === "error") {
            addSystem(`Error: ${data.error}`)
            return
        }

        renderMemory(data.memory, data.context_preview)
        addMessage("human", `Redirect (${turns} turns): ${msg}`)
        hideFinalizationOptions()
        paused = false
        setTimeout(loop, 300)
    }

    input.value = ""
    document.getElementById("command-box").classList.add("hidden")
    commandType = null
}
