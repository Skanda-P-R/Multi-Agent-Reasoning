const STEP_DELAY_MS = 500

let running = false
let paused = false
let commandType = null
let selectedModels = []
let availableModels = []
let agentModelMap = {}
let agentColorMap = {}
let loopEpoch = 0

const chat = document.getElementById("chat")
const startBtn = document.getElementById("start-btn")
const commandInput = document.getElementById("command-input")
const commandSendBtn = document.getElementById("command-send-btn")
const redirectTurnsInput = document.getElementById("redirect-turns")
const modelsDrawer = document.getElementById("models-drawer")
const modelsList = document.getElementById("models-list")
const modelCount = document.getElementById("model-count")
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

function formatAgentHeading(agent, agentModel) {
    const model = agentModel || agentModelMap[agent]
    if (!model) return `**${agent}**`
    return `**${agent}** \`${model}\``
}

function generateAgentBubble(index, total) {
    const t = Math.max(total, 2)
    const hue = Math.round((index * 360) / t)
    const hue2 = (hue + 20) % 360
    return `linear-gradient(135deg, hsla(${hue} 58% 58% / 0.88), hsla(${hue2} 62% 50% / 0.9))`
}

function setupAgentStyling(agents) {
    agentModelMap = {}
    agentColorMap = {}
    if (!Array.isArray(agents)) return
    const total = agents.length
    agents.forEach((a, idx) => {
        agentModelMap[a.name] = a.model
        agentColorMap[a.name] = generateAgentBubble(idx, total)
    })
}

function getAgentBubble(agentName) {
    if (agentColorMap[agentName]) return agentColorMap[agentName]
    const idx = Object.keys(agentColorMap).length
    const fallback = generateAgentBubble(idx, idx + 1)
    agentColorMap[agentName] = fallback
    return fallback
}

function addMessage(role, text, options = {}) {
    const div = document.createElement("div")
    div.classList.add("message")
    div.classList.add(role)
    if (options.bubbleBackground) {
        div.classList.add("agent-dynamic")
    }

    div.innerHTML = `
    <div class="bubble">
        ${marked.parse(text)}
    </div>
`
    if (options.bubbleBackground) {
        const bubble = div.querySelector(".bubble")
        bubble.style.background = options.bubbleBackground
    }

    chat.appendChild(div)
    scrollBottom()
}

function addSystem(text) {
    addMessage("system", text)
}

function toggleMemoryDrawer() {
    memoryDrawer.classList.toggle("open")
}

function toggleModelsDrawer() {
    modelsDrawer.classList.toggle("open")
}

function updateStartButtonState() {
    startBtn.disabled = selectedModels.length < 2
}

function renderModelSelector() {
    const html = availableModels.map((model) => {
        const checked = selectedModels.includes(model) ? "checked" : ""
        return `
        <label class="model-item">
            <input type="checkbox" value="${escapeHtml(model)}" ${checked} onchange="toggleModel('${model.replaceAll("'", "\\'")}')">
            <span>${escapeHtml(model)}</span>
        </label>
        `
    }).join("")

    modelsList.innerHTML = html
    modelCount.textContent = `${selectedModels.length} selected (minimum 2 required)`
    const selectAllBox = document.getElementById("select-all-models")
    if (selectAllBox) {
        selectAllBox.checked = availableModels.length > 0 && selectedModels.length === availableModels.length
    }
    updateStartButtonState()
}

function toggleModel(model) {
    if (selectedModels.includes(model)) {
        selectedModels = selectedModels.filter((m) => m !== model)
    } else {
        selectedModels.push(model)
    }
    renderModelSelector()
}

function toggleSelectAll(checked) {
    if (checked) {
        selectedModels = [...availableModels]
    } else {
        selectedModels = []
    }
    renderModelSelector()
}

async function loadModels() {
    const res = await fetch("/models")
    const data = await res.json()
    availableModels = Array.isArray(data.models) ? data.models : []
    const defaults = Array.isArray(data.default_models) ? data.default_models : []
    const validDefaults = defaults.filter((m) => availableModels.includes(m))
    selectedModels = validDefaults.length ? validDefaults : availableModels.slice(0, 4)
    renderModelSelector()
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

function isCommandReady() {
    const msg = commandInput.value.trim()
    if (!msg) return false
    if (commandType === "human_turn_redirect" || commandType === "finalize_redirect") {
        const turns = parseInt(redirectTurnsInput.value || "3", 10)
        return Number.isFinite(turns) && turns >= 1
    }
    return true
}

function updateCommandSendState() {
    commandSendBtn.disabled = !isCommandReady()
}

function showCommandBox(type, label, placeholder) {
    commandType = type
    document.getElementById("command-label").textContent = label

    commandInput.placeholder = placeholder
    commandInput.value = ""

    if (type === "human_turn_redirect" || type === "finalize_redirect") {
        redirectTurnsInput.classList.remove("hidden")
    } else {
        redirectTurnsInput.classList.add("hidden")
    }

    document.getElementById("command-box").classList.remove("hidden")
    updateCommandSendState()
    commandInput.focus()
}

async function start() {
    const q = document.getElementById("question").value.trim()
    if (!q) return
    if (selectedModels.length < 2) {
        addSystem("Select at least 2 models before starting.")
        return
    }

    chat.innerHTML = ""
    addMessage("human", q)
    memoryView.innerHTML = ""
    contextView.textContent = "(waiting for first turn)"

    const res = await fetch("/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, models: selectedModels })
    })
    const data = await res.json()
    if (data.status === "error") {
        addSystem(`Error: ${data.error}`)
        return
    }

    setupAgentStyling(data.agents)

    running = true
    paused = false
    loopEpoch += 1
    hideHumanTurnOptions()
    hideFinalizationOptions()

    loop()
}

async function loop() {
    if (!running || paused) return
    const myEpoch = loopEpoch

    const res = await fetch("/step", { method: "POST" })
    const data = await res.json()
    if (!running || paused || myEpoch !== loopEpoch) return

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
        if (candidate.text) {
            const candidateClass = (candidate.agent || "system").replace(" ", "").toLowerCase()
            addMessage(
                candidateClass,
                `${formatAgentHeading(candidate.agent || "Agent", candidate.model)}\n\n${candidate.text}`,
                { bubbleBackground: getAgentBubble(candidate.agent || "Agent") }
            )
        }
        addSystem(
            `Completion candidate by ${formatAgentHeading(candidate.agent || "Agent", candidate.model)}. Human approval required.`
        )
        showFinalizationOptions()
        return
    }

    if (data.status === "done") {
        const heading = data.agent === "System" || data.agent === "Human"
            ? `**${data.agent}**`
            : formatAgentHeading(data.agent, data.agent_model)
        addMessage("system", `${heading}\n\n${data.text}`)
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

    addMessage(
        agentClass,
        `${formatAgentHeading(data.agent, data.agent_model)}\n\n${data.text}${meta}`,
        { bubbleBackground: getAgentBubble(data.agent) }
    )

    setTimeout(loop, STEP_DELAY_MS)
}

async function pause() {
    await fetch("/pause", { method: "POST" })

    paused = true
    loopEpoch += 1
    addMessage("system", "Session paused")
}

async function resume() {
    await fetch("/resume", { method: "POST" })

    paused = false
    loopEpoch += 1
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

    const heading = data.agent === "System" || data.agent === "Human"
        ? `**${data.agent || "Human"}**`
        : formatAgentHeading(data.agent || "Agent", data.agent_model)
    addMessage("system", `${heading}\n\n${data.text || "Reasoning stopped."}`)
    hideHumanTurnOptions()
    hideFinalizationOptions()
    running = false
    paused = false
    loopEpoch += 1
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
    const heading = data.agent === "System" || data.agent === "Human"
        ? `**${data.agent}**`
        : formatAgentHeading(data.agent, data.agent_model)
    addMessage("system", `${heading}\n\n${data.text}`)
    hideFinalizationOptions()
    running = false
    paused = false
    loopEpoch += 1
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
    loopEpoch += 1
    setTimeout(loop, 300)
}

async function submitCommand() {
    const msg = commandInput.value.trim()

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
        loopEpoch += 1
        setTimeout(loop, 300)
    }

    if (commandType === "human_turn_redirect") {
        const turnsRaw = redirectTurnsInput.value
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
        loopEpoch += 1
        setTimeout(loop, 300)
    }

    if (commandType === "finalize_redirect") {
        const turnsRaw = redirectTurnsInput.value
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
        loopEpoch += 1
        setTimeout(loop, 300)
    }

    commandInput.value = ""
    document.getElementById("command-box").classList.add("hidden")
    commandType = null
    updateCommandSendState()
}

loadModels()
commandInput.addEventListener("input", updateCommandSendState)
redirectTurnsInput.addEventListener("input", updateCommandSendState)
