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
let handQueue = []
let sessionPausedForModelChanges = false
let sessionHasSaveCandidate = false
let historyContinuationPending = false
let loadedHistoryId = null

const chat = document.getElementById("chat")
const startBtn = document.getElementById("start-btn")
const modelApplyBtn = document.getElementById("model-apply-btn")
const commandInput = document.getElementById("command-input")
const commandSendBtn = document.getElementById("command-send-btn")
const redirectTurnsInput = document.getElementById("redirect-turns")
const modelsDrawer = document.getElementById("models-drawer")
const modelsList = document.getElementById("models-list")
const modelCount = document.getElementById("model-count")
const leftSidebar = document.getElementById("left-sidebar")
const memoryDrawer = document.getElementById("memory-drawer")
const queueDrawer = document.getElementById("queue-drawer")
const memoryView = document.getElementById("memory-view")
const contextView = document.getElementById("context-view")
const historyList = document.getElementById("history-list")
const historyMeta = document.getElementById("history-meta")
const questionInput = document.getElementById("question")
const suggestionBox = document.getElementById("suggestion-box")
const suggestionText = document.getElementById("suggestion-text")
const loadingIndicator = document.getElementById("loading-indicator")
const loadingText = document.getElementById("loading-text")
const detailsModal = document.getElementById("details-modal")
const detailsBody = document.getElementById("details-body")
const sessionStatusBadge = document.getElementById("session-status-badge")
const newChatBtn = document.getElementById("new-chat-btn")
let loadingCounter = 0
let loadingMessage = "Working..."
let activeSidebarSection = "models"
let sessionStatusTimer = null

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

function escapeJsString(str) {
    return String(str || "")
        .replaceAll("\\", "\\\\")
        .replaceAll("'", "\\'")
        .replaceAll("\n", "\\n")
        .replaceAll("\r", "\\r")
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
    return `hsl(${hue} 64% 58%)`
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
        bubble.style.setProperty("--agent-accent", options.bubbleBackground)
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

function setSessionStatus(text, level = "info", durationMs = 4000) {
    if (!sessionStatusBadge) return
    if (sessionStatusTimer) {
        clearTimeout(sessionStatusTimer)
        sessionStatusTimer = null
    }
    if (!text) {
        sessionStatusBadge.classList.add("hidden")
        sessionStatusBadge.classList.remove("status-info", "status-success", "status-warning", "status-error")
        return
    }
    sessionStatusBadge.classList.remove("status-info", "status-success", "status-warning", "status-error")
    sessionStatusBadge.classList.add(`status-${level}`)
    sessionStatusBadge.textContent = text
    sessionStatusBadge.classList.remove("hidden")
    if (durationMs > 0) {
        sessionStatusTimer = setTimeout(() => {
            sessionStatusBadge.classList.add("hidden")
            sessionStatusBadge.classList.remove("status-info", "status-success", "status-warning", "status-error")
            sessionStatusTimer = null
        }, durationMs)
    }
}

function setNewChatAvailability(isEnabled) {
    if (!newChatBtn) return
    newChatBtn.disabled = !isEnabled
    if (isEnabled) {
        newChatBtn.textContent = "New Chat"
        newChatBtn.title = sessionHasSaveCandidate
            ? "Save this session to history and start a new chat"
            : "Start a new chat"
    } else {
        newChatBtn.textContent = "Session running"
        newChatBtn.title = "Wait for session completion"
    }
}

function toPercent(value) {
    const num = Number(value)
    if (!Number.isFinite(num)) return "0%"
    return `${Math.round(num * 100)}%`
}

function buildTurnDetails(data) {
    const agent = data.agent || "Unknown"
    const hasIntentConfidence = data.intent_confidence !== null && typeof data.intent_confidence !== "undefined"
    const intentPriorityRaw = (data.intent_priority || "").toString().toLowerCase()
    const isBootstrap = intentPriorityRaw === "bootstrap"
    const isHumanIntent = intentPriorityRaw === "human"

    const intentHandRaiseText = isBootstrap
        ? "Bootstrap turn"
        : (typeof data.intent_hand_raise === "boolean" ? (data.intent_hand_raise ? "Yes" : "No") : "Not applicable")

    const intentPriorityText = isBootstrap
        ? "Bootstrap turn"
        : (isHumanIntent ? "Human queue turn" : (data.intent_priority || "Not applicable"))

    const intentPriorityRankText = isBootstrap
        ? "Not applicable"
        : (typeof data.intent_priority_rank === "number" ? data.intent_priority_rank : "Not applicable")

    const intentConfidenceText = isBootstrap
        ? "Not applicable"
        : (hasIntentConfidence && Number.isFinite(Number(data.intent_confidence))
            ? toPercent(Number(data.intent_confidence))
            : "Not applicable")

    const pointerRaw = (data.intent_pointer || "").trim()
    const intentPointerText = pointerRaw
        ? pointerRaw
        : (isBootstrap ? "Not applicable for bootstrap turn" : "Not provided")

    return {
        round: data.round,
        agent,
        model: data.agent_model || "n/a",
        selectionReason: data.selection_reason || "n/a",
        ignored: data.ignored ? (data.ignored_reason || "Ignored by protocol") : "No",
        quotaLeft: typeof data.quota_left === "undefined" || data.quota_left === null ? "n/a" : data.quota_left,
        handQueue: Array.isArray(data.queued_interrupts) && data.queued_interrupts.length ? data.queued_interrupts.join(", ") : "(empty)",
        intentHandRaise: intentHandRaiseText,
        intentPriority: intentPriorityText,
        intentPriorityRank: intentPriorityRankText,
        intentConfidence: intentConfidenceText,
        intentPointer: intentPointerText,
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
        renderDetailRow("Intent Hand Raise", details.intentHandRaise),
        renderDetailRow("Intent Priority", details.intentPriority),
        renderDetailRow("Intent Priority Rank", details.intentPriorityRank),
        renderDetailRow("Intent Confidence", details.intentConfidence),
        renderDetailRow("Intent Pointer", details.intentPointer),
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

function ensureSidebarOpen() {
    if (!leftSidebar) return
    leftSidebar.classList.add("expanded")
}

function toggleSidebar() {
    if (!leftSidebar) return
    leftSidebar.classList.toggle("expanded")
}

async function startNewChat() {
    if (running || paused || historyContinuationPending) return
    if (sessionHasSaveCandidate) {
        showLoading("Saving chat history...")
        const res = await fetch("/history/save_current", { method: "POST" })
        const data = await res.json()
        hideLoading()
        if (data.status === "error") {
            setSessionStatus(`Could not save chat: ${data.error || "unknown error"}`, "error", 7000)
            return
        }
        sessionHasSaveCandidate = false
        await loadHistoryList(false)
    }
    window.location.reload()
}

function openSidebarSection(section, shouldExpand = true) {
    activeSidebarSection = section
    if (shouldExpand) ensureSidebarOpen()

    const panels = document.querySelectorAll(".sidebar-panel")
    panels.forEach((panel) => {
        const isActive = panel.dataset.section === section
        panel.classList.toggle("active", isActive)
    })

    const navButtons = document.querySelectorAll(".sidebar-nav-btn")
    navButtons.forEach((btn) => btn.classList.remove("active"))
    const activeBtnId = section === "models"
        ? "nav-models"
        : section === "context"
            ? "nav-context"
            : section === "history"
                ? "nav-history"
                : "nav-queue"
    const activeBtn = document.getElementById(activeBtnId)
    if (activeBtn) activeBtn.classList.add("active")

    if (section === "history") {
        loadHistoryList()
    }
}

function toggleMemoryDrawer() {
    openSidebarSection("context")
}

function toggleQueueDrawer() {
    openSidebarSection("queue")
}

function toggleModelsDrawer() {
    setDraftFromConfigs(appliedModelConfigs)
    openSidebarSection("models")
}

function closeModelsDrawer() {
    setDraftFromConfigs(appliedModelConfigs)
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
    startBtn.disabled = running || paused || historyContinuationPending || getAppliedModelConfigs().length < 2
}

function canApplyModelSelection() {
    return (!running && !historyContinuationPending) || sessionPausedForModelChanges
}

function updateModelApplyButtonState() {
    if (!modelApplyBtn) return
    const disabled = !canApplyModelSelection()
    modelApplyBtn.disabled = disabled
    modelApplyBtn.title = disabled
        ? "Pause the session before applying model changes"
        : "Apply selected model changes"
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
    updateModelApplyButtonState()
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

function formatHistoryTimestamp(value) {
    if (!value) return "unknown time"
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return value
    return date.toLocaleString([], {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    })
}

function renderHistoryList(items) {
    if (!historyList) return
    if (!Array.isArray(items) || !items.length) {
        historyList.innerHTML = `<div class="detail-row">No saved chats yet</div>`
        if (historyMeta) historyMeta.textContent = "Saved chats appear after New Chat is clicked at the end of a session."
        return
    }

    if (historyMeta) historyMeta.textContent = `${items.length} saved chat${items.length === 1 ? "" : "s"}`
    historyList.innerHTML = items.map((item) => `
        <button type="button" class="history-item" onclick="loadPreviousChat('${escapeJsString(item.id)}')">
            <span class="history-title">${escapeHtml(item.title || "Untitled chat")}</span>
            <span class="history-time">${escapeHtml(formatHistoryTimestamp(item.saved_at))} · ${Number(item.agent_count || 0)} agents · ${Number(item.response_count || 0)} turns</span>
        </button>
    `).join("")
}

async function loadHistoryList(showStatus = true) {
    if (!historyList) return
    const res = await fetch("/history")
    const data = await res.json()
    if (data.status === "error") {
        if (showStatus) setSessionStatus("Could not load saved chats.", "error", 7000)
        return
    }
    renderHistoryList(data.items || [])
}

function buildTurnDetailsFromResponse(response) {
    return buildTurnDetails({
        round: response.round,
        agent: response.agent,
        agent_model: response.model,
        selection_reason: response.selection_reason,
        ignored: response.accepted === false,
        ignored_reason: response.ignored_reason,
        quota_left: response.quota_left,
        queued_interrupts: [],
        intent_hand_raise: response.intent_hand_raise,
        intent_priority: response.intent_priority,
        intent_priority_rank: response.intent_priority_rank,
        intent_confidence: response.intent_confidence,
        intent_pointer: response.intent_pointer,
    })
}

function renderRestoredResponse(response) {
    const agent = response.agent || "System"
    if (agent === "Human") {
        addMessage("human", response.text || "")
        return
    }
    if (agent === "System") {
        addMessage("system", `**System**\n\n${response.text || ""}`)
        return
    }
    addMessage(
        agent.replace(" ", "").toLowerCase(),
        `${formatAgentHeading(agent, response.model)}\n\n${response.text || ""}`,
        {
            bubbleBackground: getAgentBubble(agent),
            turnDetails: buildTurnDetailsFromResponse(response),
        }
    )
}

function renderTerminalHistoryEvent(events, responses) {
    const terminal = [...(events || [])].reverse().find((event) => {
        const payload = event && event.payload
        return payload && payload.status === "done" && payload.text
    })
    if (!terminal) return
    const lastResponse = Array.isArray(responses) && responses.length ? responses[responses.length - 1] : null
    if (lastResponse && lastResponse.text === terminal.payload.text) return
    const agent = terminal.payload.agent || "System"
    const heading = agent === "System" || agent === "Human"
        ? `**${agent}**`
        : formatAgentHeading(agent, terminal.payload.agent_model)
    addMessage("system", `${heading}\n\n${terminal.payload.text}`)
}

function renderLoadedHistorySession(payload) {
    const agents = payload.agents || []
    const responses = payload.responses || []
    setupAgentStyling(agents)
    appliedModelConfigs = cloneConfigs(agents.map((a) => ({
        model: a.model,
        section: a.section || defaultSection,
    })))
    setDraftFromConfigs(appliedModelConfigs)

    questionInput.value = payload.question || ""
    chat.innerHTML = ""
    addMessage("human", payload.question || "(no prompt saved)")
    responses.forEach(renderRestoredResponse)
    renderTerminalHistoryEvent(payload.history_events, responses)
    renderMemory(payload.memory, payload.context_preview)
    updateHandQueue(payload.queued_interrupts || [])
    scrollBottom()
}

async function loadPreviousChat(historyId) {
    if (running || paused || historyContinuationPending || sessionHasSaveCandidate) {
        setSessionStatus("Finish and save the current session before loading a previous chat.", "warning", 7000)
        return
    }

    showLoading("Loading saved chat...")
    const res = await fetch(`/history/${encodeURIComponent(historyId)}/load`, { method: "POST" })
    const data = await res.json()
    hideLoading()
    if (data.status === "error") {
        setSessionStatus(`Could not load chat: ${data.error || "unknown error"}`, "error", 7000)
        return
    }

    loadedHistoryId = historyId
    historyContinuationPending = true
    running = false
    paused = true
    sessionPausedForModelChanges = false
    sessionHasSaveCandidate = false
    renderLoadedHistorySession(data.session || {})
    hideHumanTurnOptions()
    hideFinalizationOptions()
    updateStartButtonState()
    updateModelApplyButtonState()
    setNewChatAvailability(false)
    showCommandBox("history_redirect", "CONTINUE SAVED CHAT: REDIRECT", "Enter redirect objective to continue this saved chat...")
    setSessionStatus("Loaded saved chat. Add a redirect to continue.", "info", 0)
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
    if (commandType === "human_turn_redirect" || commandType === "finalize_redirect" || commandType === "history_redirect") {
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

    if (type === "human_turn_redirect" || type === "finalize_redirect" || type === "history_redirect") {
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
        setSessionStatus("Unable to load models right now.", "error", 7000)
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
        setSessionStatus("Select at least 2 models before starting.", "warning")
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
        setSessionStatus("Could not start session.", "error", 7000)
        setNewChatAvailability(true)
        hideLoading()
        return
    }

    appliedModelConfigs = cloneConfigs(finalConfigs)
    setDraftFromConfigs(appliedModelConfigs)
    setupAgentStyling(data.agents)

    running = true
    paused = false
    sessionPausedForModelChanges = false
    historyContinuationPending = false
    loadedHistoryId = null
    sessionHasSaveCandidate = true
    updateModelApplyButtonState()
    updateStartButtonState()
    setNewChatAvailability(false)
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
        setSessionStatus("Select at least 2 models before starting.", "warning")
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
        setSessionStatus("Review auto-selected models. Approve or edit manually.", "info", 0)
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
        setSessionStatus("Suggested selection has fewer than 2 valid models.", "warning")
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
    openSidebarSection("models")
    setSessionStatus("Manual model selection enabled. Press Apply to start.", "info", 0)
}

async function applyModelSelection() {
    const selected = getSelectedModelConfigs()
    if (selected.length < 2) {
        setSessionStatus("Select at least 2 model-section entries.", "warning")
        return
    }

    if (!canApplyModelSelection()) {
        setSessionStatus("Pause the session before applying model changes.", "warning")
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
            setSessionStatus("Could not apply model changes.", "error", 7000)
            return
        }
        setupAgentStyling(data.agents || [])
        setSessionStatus(`Applied ${selected.length} model-section entries.`, "success")
    }

    appliedModelConfigs = cloneConfigs(selected)
    closeModelsDrawer()

    const shouldAutoStartManualFlow = !running && pendingSuggestion && manualSelectionChosen
    if (shouldAutoStartManualFlow) {
        const q = questionInput.value.trim()
        if (!q) {
            setSessionStatus("Question is empty. Enter a question to start.", "warning")
            return
        }
        manualSelectionChosen = false
        pendingSuggestion = null
        suggestionBox.classList.add("hidden")
        leftSidebar?.classList.remove("expanded")
        await beginSession(q, selected)
    }
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
    updateHandQueue(data.queued_interrupts)

    if (data.status === "error") {
        setSessionStatus("Session paused due to an internal error.", "error", 7000)
        running = false
        sessionHasSaveCandidate = false
        sessionPausedForModelChanges = false
        updateModelApplyButtonState()
        updateStartButtonState()
        setNewChatAvailability(true)
        clearLoading()
        return
    }

    if (data.status === "paused") {
        paused = true
        sessionPausedForModelChanges = true
        updateModelApplyButtonState()
        updateStartButtonState()
        clearLoading()
        return
    }

    if (data.status === "awaiting_human_turn") {
        paused = true
        sessionPausedForModelChanges = false
        updateModelApplyButtonState()
        updateStartButtonState()
        setSessionStatus("Human turn selected. Choose Inject or Redirect.", "info", 0)
        showHumanTurnOptions()
        clearLoading()
        return
    }

    if (data.status === "awaiting_human_finalization") {
        paused = true
        sessionPausedForModelChanges = false
        updateModelApplyButtonState()
        updateStartButtonState()
        const candidate = data.finalization_candidate || {}
        if (candidate.text) {
            const candidateClass = (candidate.agent || "system").replace(" ", "").toLowerCase()
            const candidateDetailsPayload = {
                ...data,
                agent: candidate.agent || "Agent",
                agent_model: candidate.model || "n/a",
                text: candidate.text,
            }
            addMessage(
                candidateClass,
                `${formatAgentHeading(candidate.agent || "Agent", candidate.model)}\n\n${candidate.text}`,
                {
                    bubbleBackground: getAgentBubble(candidate.agent || "Agent"),
                    turnDetails: buildTurnDetails(candidateDetailsPayload),
                }
            )
        }
        setSessionStatus("Completion candidate ready. Approval required.", "info", 0)
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
        sessionPausedForModelChanges = false
        updateModelApplyButtonState()
        updateStartButtonState()
        setNewChatAvailability(true)
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
    sessionPausedForModelChanges = true
    updateModelApplyButtonState()
    updateStartButtonState()
    loopEpoch += 1
    clearLoading()
    setSessionStatus("Session paused", "warning")
}

async function resume() {
    if (historyContinuationPending) {
        setSessionStatus("Loaded chats must continue with a redirect.", "warning", 7000)
        return
    }
    showLoading("Resuming session...")
    await fetch("/resume", { method: "POST" })
    hideLoading()

    paused = false
    sessionPausedForModelChanges = false
    updateModelApplyButtonState()
    updateStartButtonState()
    loopEpoch += 1
    setSessionStatus("Session resumed", "success")

    loop()
}

async function stopReasoning() {
    showLoading("Stopping session...")
    const res = await fetch("/stop", { method: "POST" })
    const data = await res.json()
    hideLoading()

    if (data.status === "error") {
        setSessionStatus("Could not stop session.", "error", 7000)
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
    sessionPausedForModelChanges = false
    updateModelApplyButtonState()
    updateStartButtonState()
    setNewChatAvailability(true)
    loopEpoch += 1
    clearLoading()
}

async function raiseHand() {
    showLoading("Raising hand...")
    const res = await fetch("/raise_hand", { method: "POST" })
    const data = await res.json()
    hideLoading()

    if (data.status === "error") {
        setSessionStatus("Could not raise hand.", "error", 7000)
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
        setSessionStatus("Could not finalize completion.", "error", 7000)
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
    sessionPausedForModelChanges = false
    updateModelApplyButtonState()
    updateStartButtonState()
    setNewChatAvailability(true)
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
        setSessionStatus("Could not continue iteration.", "error", 7000)
        return
    }

    renderMemory(data.memory, data.context_preview)
    hideFinalizationOptions()
    paused = false
    sessionPausedForModelChanges = false
    updateModelApplyButtonState()
    updateStartButtonState()
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
            setSessionStatus("Inject failed.", "error", 7000)
            return
        }

        renderMemory(data.memory, data.context_preview)
        updateHandQueue(data.queued_interrupts)
        addMessage("human", `Inject: ${msg}`)
        hideHumanTurnOptions()
        paused = false
        sessionPausedForModelChanges = false
        updateModelApplyButtonState()
        updateStartButtonState()
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
            setSessionStatus("Redirect failed.", "error", 7000)
            return
        }

        renderMemory(data.memory, data.context_preview)
        updateHandQueue(data.queued_interrupts)
        addMessage("human", `Redirect (${turns} turns): ${msg}`)
        hideHumanTurnOptions()
        paused = false
        sessionPausedForModelChanges = false
        updateModelApplyButtonState()
        updateStartButtonState()
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
            setSessionStatus("Finalize redirect failed.", "error", 7000)
            return
        }

        renderMemory(data.memory, data.context_preview)
        updateHandQueue(data.queued_interrupts)
        addMessage("human", `Redirect (${turns} turns): ${msg}`)
        hideFinalizationOptions()
        paused = false
        sessionPausedForModelChanges = false
        updateModelApplyButtonState()
        updateStartButtonState()
        loopEpoch += 1
        setTimeout(loop, 300)
    }

    if (commandType === "history_redirect") {
        const turnsRaw = redirectTurnsInput.value
        const turns = Math.max(1, parseInt(turnsRaw || "3", 10))

        showLoading("Continuing saved chat...")
        const res = await fetch("/history/continue", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: msg, turns })
        })
        const data = await res.json()
        hideLoading()

        if (data.status === "error") {
            setSessionStatus("Saved chat continuation failed.", "error", 7000)
            return
        }

        renderMemory(data.memory, data.context_preview)
        updateHandQueue(data.queued_interrupts)
        addMessage("human", `Redirect (${turns} turns): ${msg}`)
        historyContinuationPending = false
        sessionHasSaveCandidate = true
        running = true
        paused = false
        sessionPausedForModelChanges = false
        updateModelApplyButtonState()
        updateStartButtonState()
        setNewChatAvailability(false)
        loopEpoch += 1
        setTimeout(loop, 300)
    }

    commandInput.value = ""
    document.getElementById("command-box").classList.add("hidden")
    commandType = null
    updateCommandSendState()
}

function updateHandQueue(queue) {
    if (!Array.isArray(queue)) return
    handQueue = [...queue]
    renderQueuePanel()
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
            const arrow = idx < handQueue.length - 1 ? `<div class="queue-arrow">&darr;</div>` : ""
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
openSidebarSection(activeSidebarSection, false)
setNewChatAvailability(true)
updateModelApplyButtonState()
loadHistoryList(false)
commandInput.addEventListener("input", updateCommandSendState)
redirectTurnsInput.addEventListener("input", updateCommandSendState)
questionInput.addEventListener("input", () => {
    if (questionInput.value.trim() !== suggestedQuestion) {
        resetSuggestionState()
    }
})
