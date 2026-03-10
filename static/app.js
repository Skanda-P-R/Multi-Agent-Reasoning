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

    loop()


}

async function loop() {


    if (!running || paused) return

    const res = await fetch("/step", { method: "POST" })
    const data = await res.json()

    if (data.status === "paused") {
        paused = true
        return
    }

    const agentClass = data.agent.replace(" ", "").toLowerCase()

    addMessage(agentClass, `**${data.agent}**\n\n${data.text}`)

    setTimeout(loop, 900)


}

async function pause() {


    await fetch("/pause", { method: "POST" })

    paused = true

    addMessage("system", "⏸️ Session paused")


}

async function resume() {


    await fetch("/resume", { method: "POST" })

    paused = false

    addMessage("system", "▶️ Session resumed")

    loop()


}

function showInject() {


    commandType = "inject"

    document.getElementById("command-box").classList.remove("hidden")


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

        addMessage("human", `🧑 Inject: ${msg}`)
    }


    input.value = ""

    document.getElementById("command-box").classList.add("hidden")


}
