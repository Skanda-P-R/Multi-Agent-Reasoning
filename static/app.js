const STEP_DELAY_MS = 500

let running = false
let paused = false
let commandType = null
let availableModels = []
let availableSections = []
let defaultSection = "general"
let modelSectionMap = {}
let appliedModelConfigs = []
let pendingSuggestion = null
let suggestionApproved = false
let manualSelectionChosen = false
let suggestedQuestion = ""
let approvedSuggestedModels = null
let agentModelMap = {}
let agentColorMap = {}
let loopEpoch = 0
let agentScores = {}
let handRaiseScores = {}
let handQueue = []

const chat = document.getElementById("chat")
const startBtn = document.getElementById("start-btn")
const commandInput = document.getElementById("command-input")
const commandSendBtn = document.getElementById("command-send-btn")
const redirectTurnsInput = document.getElementById("redirect-turns")
const modelsDrawer = document.getElementById("models-drawer")
const modelsList = document.getElementById("models-list")
const modelCount = document.getElementById("model-count")
const memoryDrawer = document.getElementById("memory-drawer")
const scoresDrawer = document.getElementById("scores-drawer")
const queueDrawer = document.getElementById("queue-drawer")
const memoryView = document.getElementById("memory-view")
const contextView = document.getElementById("context-view")
const questionInput = document.getElementById("question")
const suggestionBox = document.getElementById("suggestion-box")
const suggestionText = document.getElementById("suggestion-text")
const loadingIndicator = document.getElementById("loading-indicator")
const loadingText = document.getElementById("loading-text")
const detailsModal = document.getElementById("details-modal")
const detailsBody = document.getElementById("details-body")
let loadingCounter = 0
let loadingMessage = "Working..."

function showLoading(message = "Working...") {
    loadingCounter += 1
    loadingMessage = message
    loadingText.textContent = loadingMessage
    loadingIndicator.classList.remove("hidden")
}

function hideLoading() {
    loadingCounter = Math.max(0, loadingCounter - 1)
    if (loadingCounter === 0) {
        loadingIndicator.classList.add("hidden")
        loadingText.textContent = "Working..."
    } else {
        loadingText.textContent = loadingMessage
    }
}

function setLoadingMessage(message) {
    loadingMessage = message || "Working..."
    if (!loadingIndicator.classList.contains("hidden")) {
        loadingText.textContent = loadingMessage
    }
}

function clearLoading() {
    loadingCounter = 0
    loadingIndicator.classList.add("hidden")
    loadingText.textContent = "Working..."
}

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
    const info = agentModel || agentModelMap[agent]
    if (!info) return `**${agent}**`
    if (typeof info === "string") return `**${agent}** \`${info}\``
    const model = info.model || ""
    const section = info.section || defaultSection
    return `**${agent}** \`${model}\` _(${section})_`
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
        agentModelMap[a.name] = { model: a.model, section: a.section || defaultSection }
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

    if (options.turnDetails) {
        const btn = document.createElement("button")
        btn.type = "button"
        btn.className = "message-details-btn"
        btn.textContent = "View Turn Details"
        btn.addEventListener("click", () => openDetailsModal(options.turnDetails))
        div.appendChild(btn)
    }

    chat.appendChild(div)
    scrollBottom()
}

function addSystem(text) {
    addMessage("system", text)
}

function toPercent(value) {
    const num = Number(value)
    if (!Number.isFinite(num)) return "0%"
    return `${Math.round(num * 100)}%`
}

function buildTurnDetails(data) {
    const agent = data.agent || "Unknown"
    const breakdown = handRaiseScores[agent] || {}
    const score = agentScores[agent]
    return {
        round: data.round,
        agent,
        model: data.agent_model || "n/a",
        selectionReason: data.selection_reason || "n/a",
        ignored: data.ignored ? (data.ignored_reason || "Ignored by protocol") : "No",
        quotaLeft: typeof data.quota_left === "undefined" || data.quota_left === null ? "n/a" : data.quota_left,
        handQueue: Array.isArray(data.queued_interrupts) && data.queued_interrupts.length ? data.queued_interrupts.join(", ") : "(empty)",
        score: Number.isFinite(score) ? toPercent(score) : "n/a",
        relevance: Number.isFinite(breakdown.relevance) ? toPercent(breakdown.relevance) : "n/a",
        novelty: Number.isFinite(breakdown.novelty) ? toPercent(breakdown.novelty) : "n/a",
        confidence: Number.isFinite(breakdown.confidence) ? toPercent(breakdown.confidence) : "n/a",
        fairness: Number.isFinite(breakdown.fairness) ? toPercent(breakdown.fairness) : "n/a",
    }
}

function renderDetailRow(label, value) {
    return `<div class="detail-row"><span class="detail-label">${escapeHtml(label)}:</span> ${escapeHtml(String(value))}</div>`
}

function openDetailsModal(details) {
    if (!detailsModal || !detailsBody) return
    const html = [
        renderDetailRow("Round", details.round),
        renderDetailRow("Agent", details.agent),
        renderDetailRow("Model", details.model),
        renderDetailRow("Selection Reason", details.selectionReason),
        renderDetailRow("Ignored", details.ignored),
        renderDetailRow("Quota Left", details.quotaLeft),
        renderDetailRow("Hand Queue", details.handQueue),
        renderDetailRow("Final Hand-Raise Score", details.score),
        renderDetailRow("Relevance", details.relevance),
        renderDetailRow("Novelty", details.novelty),
        renderDetailRow("Confidence", details.confidence),
        renderDetailRow("Fairness", details.fairness),
    ].join("")
    detailsBody.innerHTML = `<div class="detail-grid">${html}</div>`
    detailsModal.classList.remove("hidden")
}

function closeDetailsModal(event, force = false) {
    if (force) {
        if (!detailsModal) return
        detailsModal.classList.add("hidden")
        return
    }
    if (
        event &&
        event.target &&
        event.target.classList &&
        !event.target.classList.contains("details-modal") &&
        event.target.closest(".details-dialog")
    ) {
        return
    }
    if (!detailsModal) return
    detailsModal.classList.add("hidden")
}

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && detailsModal && !detailsModal.classList.contains("hidden")) {
        detailsModal.classList.add("hidden")
    }
})

function toggleMemoryDrawer() {
    memoryDrawer.classList.toggle("open")
}

function toggleScoresDrawer() {
    scoresDrawer.classList.toggle("open")
}

function toggleQueueDrawer() {
    queueDrawer.classList.toggle("open")
}

function toggleModelsDrawer() {
    if (modelsDrawer.classList.contains("open")) {
        closeModelsDrawer()
        return
    }
    setDraftFromConfigs(appliedModelConfigs)
    modelsDrawer.classList.add("open")
}

function closeModelsDrawer() {
    setDraftFromConfigs(appliedModelConfigs)
    modelsDrawer.classList.remove("open")
}

function cloneConfigs(configs) {
    return (configs || []).map((c) => ({ model: c.model, section: c.section }))
}

function setDraftFromConfigs(configs) {
    modelSectionMap = {}
    for (const model of availableModels) modelSectionMap[model] = []
    for (const item of configs || []) {
        if (!item || !availableModels.includes(item.model)) continue
        const existing = Array.isArray(modelSectionMap[item.model]) ? modelSectionMap[item.model] : []
        setModelSections(item.model, [...existing, item.section || defaultSection])
    }
    renderModelSelector()
}

function getAppliedModelConfigs() {
    if (Array.isArray(appliedModelConfigs) && appliedModelConfigs.length) {
        return cloneConfigs(appliedModelConfigs)
    }
    return getSelectedModelConfigs()
}

function updateStartButtonState() {
    startBtn.disabled = getAppliedModelConfigs().length < 2
}

function renderModelSelector() {
    const sectionOptions = availableSections.map((section) => (
        `<option value="${escapeHtml(section)}">${escapeHtml(section)}</option>`
    )).join("")

    const html = availableModels.map((model) => {
        const sections = Array.isArray(modelSectionMap[model]) ? modelSectionMap[model] : []
        const checked = sections.length > 0 ? "checked" : ""
        const modelEscaped = model.replaceAll("'", "\\'")
        const sectionRows = sections.map((section, idx) => `
            <div class="section-row">
                <select class="section-select" onchange="updateModelSection('${modelEscaped}', ${idx}, this.value)">
                    ${sectionOptions}
                </select>
                <button type="button" class="btn btn-muted section-remove-btn" onclick="removeModelSection('${modelEscaped}', ${idx})">Remove</button>
            </div>
        `).join("")
        return `
        <div class="model-item">
            <div class="model-main-row">
                <input type="checkbox" value="${escapeHtml(model)}" ${checked} onchange="toggleModel('${modelEscaped}')">
                <span>${escapeHtml(model)}</span>
            </div>
            ${sections.length ? `
            <div class="model-sections">
                ${sectionRows}
                <button type="button" class="btn btn-muted section-add-btn" onclick="addModelSection('${modelEscaped}')">Add Section</button>
            </div>
            ` : ""}
        </div>
        `
    }).join("")

    modelsList.innerHTML = html
    for (const row of modelsList.querySelectorAll(".model-item")) {
        const checkbox = row.querySelector("input[type='checkbox']")
        if (!checkbox) continue
        const model = checkbox.value
        const sections = Array.isArray(modelSectionMap[model]) ? modelSectionMap[model] : []
        const selects = row.querySelectorAll(".section-select")
        selects.forEach((select, idx) => {
            select.value = sections[idx] || defaultSection
        })
    }

    const selectedEntries = getSelectedModelConfigs().length
    const selectedModels = availableModels.filter((m) => (modelSectionMap[m] || []).length > 0).length
    modelCount.textContent = `${selectedEntries} agents selected across ${selectedModels} models (minimum 2 required)`
    const selectAllBox = document.getElementById("select-all-models")
    if (selectAllBox) {
        selectAllBox.checked = availableModels.length > 0 && selectedModels === availableModels.length
    }
    updateStartButtonState()
}

function normalizeSection(section) {
    return availableSections.includes(section) ? section : defaultSection
}

function dedupeSections(sections) {
    const out = []
    for (const s of sections || []) {
        const section = normalizeSection(s)
        if (!out.includes(section)) out.push(section)
    }
    return out
}

function setModelSections(model, sections) {
    modelSectionMap[model] = dedupeSections(sections)
}

function toggleModel(model) {
    const existing = Array.isArray(modelSectionMap[model]) ? modelSectionMap[model] : []
    if (existing.length > 0) {
        setModelSections(model, [])
    } else {
        setModelSections(model, [defaultSection])
    }
    suggestionApproved = false
    approvedSuggestedModels = null
    if (pendingSuggestion) manualSelectionChosen = true
    renderModelSelector()
}

function toggleSelectAll(checked) {
    if (checked) {
        for (const model of availableModels) {
            setModelSections(model, [defaultSection])
        }
    } else {
        for (const model of availableModels) {
            setModelSections(model, [])
        }
    }
    suggestionApproved = false
    approvedSuggestedModels = null
    if (pendingSuggestion) manualSelectionChosen = true
    renderModelSelector()
}

function addModelSection(model) {
    const existing = Array.isArray(modelSectionMap[model]) ? modelSectionMap[model] : []
    const sectionToAdd = availableSections.find((s) => !existing.includes(s)) || defaultSection
    setModelSections(model, [...existing, sectionToAdd])
    suggestionApproved = false
    approvedSuggestedModels = null
    if (pendingSuggestion) manualSelectionChosen = true
    renderModelSelector()
}

function updateModelSection(model, index, section) {
    const existing = Array.isArray(modelSectionMap[model]) ? [...modelSectionMap[model]] : []
    if (index < 0 || index >= existing.length) return
    existing[index] = normalizeSection(section)
    setModelSections(model, existing)
    suggestionApproved = false
    approvedSuggestedModels = null
    if (pendingSuggestion) manualSelectionChosen = true
    renderModelSelector()
}

function removeModelSection(model, index) {
    const existing = Array.isArray(modelSectionMap[model]) ? [...modelSectionMap[model]] : []
    if (index < 0 || index >= existing.length) return
    existing.splice(index, 1)
    setModelSections(model, existing)
    suggestionApproved = false
    approvedSuggestedModels = null
    if (pendingSuggestion) manualSelectionChosen = true
    renderModelSelector()
}

function getSelectedModelConfigs() {
    const configs = []
    for (const model of availableModels) {
        const sections = Array.isArray(modelSectionMap[model]) ? modelSectionMap[model] : []
        for (const section of sections) {
            configs.push({
                model,
                section: normalizeSection(section)
            })
        }
    }
    return configs
}

async function loadModels() {
    showLoading("Loading available models...")
    const res = await fetch("/models")
    const data = await res.json()
    availableModels = Array.isArray(data.models) ? data.models : []
    availableSections = Array.isArray(data.sections) && data.sections.length ? data.sections : ["general"]
    defaultSection = data.default_section || availableSections[0] || "general"

    modelSectionMap = {}
    for (const model of availableModels) modelSectionMap[model] = []

    const defaults = Array.isArray(data.default_models) ? data.default_models : []
    const normalizedDefaults = defaults.map((item) => {
        if (typeof item === "string") return { model: item, section: defaultSection }
        return {
            model: item.model,
            section: item.section || defaultSection
        }
    }).filter((item) => item.model && availableModels.includes(item.model))

    for (const item of normalizedDefaults) {
        const existing = Array.isArray(modelSectionMap[item.model]) ? modelSectionMap[item.model] : []
        setModelSections(item.model, [...existing, item.section || defaultSection])
    }

    if (getSelectedModelConfigs().length < 2) {
        for (const model of availableModels.slice(0, 4)) {
            setModelSections(model, [defaultSection])
        }
    }

    appliedModelConfigs = cloneConfigs(getSelectedModelConfigs())
    renderModelSelector()
    hideLoading()
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

function resetSuggestionState() {
    pendingSuggestion = null
    suggestionApproved = false
    manualSelectionChosen = false
    suggestedQuestion = ""
    approvedSuggestedModels = null
    suggestionBox.classList.add("hidden")
}

function renderSuggestionPanel(suggestion) {
    const items = Array.isArray(suggestion.models) ? suggestion.models : []
    const list = items.map((m) => `- ${m.model} (${m.section || defaultSection})`).join("\n")
    suggestionText.textContent =
        `Suggested section: ${suggestion.section || defaultSection}\n` +
        `Panel size: ${items.length}\n\n${list}`
    suggestionBox.classList.remove("hidden")
}

async function fetchSuggestion(question) {
    showLoading("Analyzing prompt and selecting models...")
    const res = await fetch("/suggest_models", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question })
    })
    const data = await res.json()
    if (data.status === "error") {
        addSystem(`Error: ${data.error}`)
        hideLoading()
        return null
    }
    hideLoading()
    return data.suggestion || null
}

function applySuggestedModels() {
    if (!pendingSuggestion || !Array.isArray(pendingSuggestion.models)) return

    const normalized = []
    const seenPairs = new Set()
    for (const item of pendingSuggestion.models) {
        if (!item || !availableModels.includes(item.model)) continue
        const section = availableSections.includes(item.section) ? item.section : defaultSection
        const key = `${item.model}::${section}`
        if (seenPairs.has(key)) continue
        seenPairs.add(key)
        normalized.push({ model: item.model, section })
    }
    approvedSuggestedModels = normalized

    // Preload drawer draft with suggestion (applied only on Select Models or Approve Suggested).
    setDraftFromConfigs(normalized)
}

async function beginSession(question, modelConfigs = null) {
    const finalConfigs = Array.isArray(modelConfigs) ? modelConfigs : getAppliedModelConfigs()
    if (finalConfigs.length < 2) {
        addSystem("Select at least 2 models before starting.")
        return
    }

    if (!question) return

    chat.innerHTML = ""
    addMessage("human", question)
    memoryView.innerHTML = ""
    contextView.textContent = "(waiting for first turn)"
    handQueue = []
    renderQueuePanel()
    suggestionBox.classList.add("hidden")

    showLoading("Starting session...")
    const res = await fetch("/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, models: finalConfigs })
    })
    const data = await res.json()
    if (data.status === "error") {
        addSystem(`Error: ${data.error}`)
        hideLoading()
        return
    }

    appliedModelConfigs = cloneConfigs(finalConfigs)
    setDraftFromConfigs(appliedModelConfigs)
    setupAgentStyling(data.agents)

    running = true
    paused = false
    loopEpoch += 1
    hideHumanTurnOptions()
    hideFinalizationOptions()
    hideLoading()

    loop()
}

async function start() {
    const q = questionInput.value.trim()
    if (!q) return
    if (getAppliedModelConfigs().length < 2) {
        addSystem("Select at least 2 models before starting.")
        return
    }

    if (suggestedQuestion !== q) {
        resetSuggestionState()
    }

    if (!pendingSuggestion) {
        pendingSuggestion = await fetchSuggestion(q)
        suggestedQuestion = q
        if (!pendingSuggestion) return
        renderSuggestionPanel(pendingSuggestion)
        addSystem("Review auto-selected models. Approve suggestion or edit manually.")
        return
    }

    if (!suggestionApproved && !manualSelectionChosen) {
        renderSuggestionPanel(pendingSuggestion)
        return
    }

    await beginSession(q)
}

async function approveSuggestedModels() {
    if (!pendingSuggestion) return
    applySuggestedModels()
    if (!approvedSuggestedModels || approvedSuggestedModels.length < 2) {
        addSystem("Suggested selection has fewer than 2 valid model entries.")
        return
    }
    suggestionApproved = true
    manualSelectionChosen = false
    suggestionBox.classList.add("hidden")
    const q = questionInput.value.trim()
    if (!q) return
    appliedModelConfigs = cloneConfigs(approvedSuggestedModels)
    await beginSession(q, approvedSuggestedModels)
}

function useManualSelection() {
    if (pendingSuggestion) {
        applySuggestedModels()
    }
    manualSelectionChosen = true
    suggestionApproved = false
    approvedSuggestedModels = null
    suggestionBox.classList.add("hidden")
    modelsDrawer.classList.add("open")
    addSystem("Manual model selection enabled. Choose models/sections, then press Start.")
}

async function applyModelSelection() {
    const selected = getSelectedModelConfigs()
    if (selected.length < 2) {
        addSystem("Select at least 2 model-section entries.")
        return
    }

    if (running && !paused) {
        addSystem("Pause the session before applying model changes.")
        return
    }

    if (running && paused) {
        showLoading("Applying model changes...")
        const res = await fetch("/set_models", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ models: selected })
        })
        const data = await res.json()
        hideLoading()
        if (data.status === "error") {
            addSystem(`Error: ${data.error}`)
            return
        }
        setupAgentStyling(data.agents || [])
        addSystem(`Applied ${selected.length} model-section entries to the paused session.`)
    }

    appliedModelConfigs = cloneConfigs(selected)
    closeModelsDrawer()
}

async function loop() {
    if (!running || paused) {
        clearLoading()
        return
    }
    const myEpoch = loopEpoch

    showLoading("Waiting for next agent response...")
    const res = await fetch("/step", { method: "POST" })
    const data = await res.json()
    hideLoading()
    if (!running || paused || myEpoch !== loopEpoch) return

    renderMemory(data.memory, data.context_preview)
    updateAgentScores(data.agent_scores, data.hand_raise_scores)
    updateHandQueue(data.queued_interrupts)

    if (data.status === "error") {
        addSystem(`Error: ${data.error || "Unknown server error"}`)
        running = false
        clearLoading()
        return
    }

    if (data.status === "paused") {
        paused = true
        clearLoading()
        return
    }

    if (data.status === "awaiting_human_turn") {
        paused = true
        addSystem("Human turn selected. Choose Inject or Redirect.")
        showHumanTurnOptions()
        clearLoading()
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
        clearLoading()
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
        clearLoading()
        return
    }

    const agentClass = data.agent.replace(" ", "").toLowerCase()

    addMessage(
        agentClass,
        `${formatAgentHeading(data.agent, data.agent_model)}\n\n${data.text}`,
        {
            bubbleBackground: getAgentBubble(data.agent),
            turnDetails: buildTurnDetails(data),
        }
    )

    showLoading("Preparing next turn...")
    setTimeout(loop, STEP_DELAY_MS)
}

async function pause() {
    showLoading("Pausing session...")
    await fetch("/pause", { method: "POST" })
    hideLoading()

    paused = true
    loopEpoch += 1
    clearLoading()
    addMessage("system", "Session paused")
}

async function resume() {
    showLoading("Resuming session...")
    await fetch("/resume", { method: "POST" })
    hideLoading()

    paused = false
    loopEpoch += 1
    addMessage("system", "Session resumed")

    loop()
}

async function stopReasoning() {
    showLoading("Stopping session...")
    const res = await fetch("/stop", { method: "POST" })
    const data = await res.json()
    hideLoading()

    if (data.status === "error") {
        addSystem(`Error: ${data.error}`)
        return
    }

    if (data.memory || data.context_preview) {
        renderMemory(data.memory, data.context_preview)
    }
    updateHandQueue(data.queued_interrupts)

    const heading = data.agent === "System" || data.agent === "Human"
        ? `**${data.agent || "Human"}**`
        : formatAgentHeading(data.agent || "Agent", data.agent_model)
    addMessage("system", `${heading}\n\n${data.text || "Reasoning stopped."}`)
    hideHumanTurnOptions()
    hideFinalizationOptions()
    running = false
    paused = false
    loopEpoch += 1
    clearLoading()
}

async function raiseHand() {
    showLoading("Raising hand...")
    const res = await fetch("/raise_hand", { method: "POST" })
    const data = await res.json()
    hideLoading()

    if (data.status === "error") {
        addSystem(`Error: ${data.error}`)
        return
    }

    if (!handQueue.includes("Human")) {
        handQueue.push("Human")
        renderQueuePanel()
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
    showLoading("Finalizing completion...")
    const res = await fetch("/finalize/approve", { method: "POST" })
    const data = await res.json()
    hideLoading()
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
    showLoading("Continuing iteration...")
    const res = await fetch("/finalize/continue", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
    })
    const data = await res.json()
    hideLoading()
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
        showLoading("Submitting inject...")
        const res = await fetch("/human_turn", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "inject", message: msg })
        })
        const data = await res.json()
        hideLoading()

        if (data.status === "error") {
            addSystem(`Error: ${data.error}`)
            return
        }

        renderMemory(data.memory, data.context_preview)
        updateHandQueue(data.queued_interrupts)
        addMessage("human", `Inject: ${msg}`)
        hideHumanTurnOptions()
        paused = false
        loopEpoch += 1
        setTimeout(loop, 300)
    }

    if (commandType === "human_turn_redirect") {
        const turnsRaw = redirectTurnsInput.value
        const turns = Math.max(1, parseInt(turnsRaw || "3", 10))

        showLoading("Submitting redirect...")
        const res = await fetch("/human_turn", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "redirect", message: msg, turns })
        })
        const data = await res.json()
        hideLoading()

        if (data.status === "error") {
            addSystem(`Error: ${data.error}`)
            return
        }

        renderMemory(data.memory, data.context_preview)
        updateHandQueue(data.queued_interrupts)
        addMessage("human", `Redirect (${turns} turns): ${msg}`)
        hideHumanTurnOptions()
        paused = false
        loopEpoch += 1
        setTimeout(loop, 300)
    }

    if (commandType === "finalize_redirect") {
        const turnsRaw = redirectTurnsInput.value
        const turns = Math.max(1, parseInt(turnsRaw || "3", 10))

        showLoading("Applying redirect and continuing...")
        const res = await fetch("/finalize/continue", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ redirect_message: msg, turns })
        })
        const data = await res.json()
        hideLoading()

        if (data.status === "error") {
            addSystem(`Error: ${data.error}`)
            return
        }

        renderMemory(data.memory, data.context_preview)
        updateHandQueue(data.queued_interrupts)
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

// Score display functions
function updateAgentScores(scores, breakdowns) {
    agentScores = scores || {}
    handRaiseScores = breakdowns || {}
    renderScoresPanel()
}

function updateHandQueue(queue) {
    if (!Array.isArray(queue)) return
    handQueue = [...queue]
    renderQueuePanel()
}

function renderScoresPanel() {
    const scoresList = document.getElementById("scores-list")
    if (!scoresList) return

    const html = Object.entries(agentScores)
        .map(([agentName, score]) => {
            const breakdown = handRaiseScores[agentName] || {}
            const scorePercentage = Math.round(score * 100)
            return `
            <div class="score-item" title="Relevance: ${(breakdown.relevance || 0).toFixed(2)}, Novelty: ${(breakdown.novelty || 0).toFixed(2)}, Confidence: ${(breakdown.confidence || 0).toFixed(2)}, Fairness: ${(breakdown.fairness || 0).toFixed(2)}">
                <span class="agent-name">${escapeHtml(agentName)}</span>
                <span class="agent-score">${scorePercentage}%</span>
            </div>
        `})
        .join("")
    
    scoresList.innerHTML = html || "<p>No scores yet</p>"
}

function renderQueuePanel() {
    const queueTrack = document.getElementById("queue-track")
    if (!queueTrack) return
    const queueMeta = document.getElementById("queue-meta")

    if (!handQueue.length) {
        queueTrack.innerHTML = `<div class="detail-row">Queue is empty</div>`
        if (queueMeta) queueMeta.textContent = "Queue updates each protocol turn."
        return
    }

    if (queueMeta) {
        queueMeta.textContent = `Front of queue: ${handQueue[0]}`
    }

    const html = handQueue
        .map((agent, idx) => {
            const arrow = idx < handQueue.length - 1 ? `<div class="queue-arrow">↓</div>` : ""
            return `
            <div class="queue-item">
                <span class="queue-pos">${idx + 1}</span>
                <span class="queue-agent">${escapeHtml(agent)}</span>
            </div>
            ${arrow}
        `
        })
        .join("")

    queueTrack.innerHTML = html
}

loadModels()
commandInput.addEventListener("input", updateCommandSendState)
redirectTurnsInput.addEventListener("input", updateCommandSendState)
questionInput.addEventListener("input", () => {
    if (questionInput.value.trim() !== suggestedQuestion) {
        resetSuggestionState()
    }
})
