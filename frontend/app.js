/**
 * Agente IA — Logica del chat frontend
 * Fetch a POST /chat, Enter para enviar, auto-scroll, animaciones
 */

// --- Elementos del DOM ---
const chatMessages = document.getElementById('chatMessages');
const chatContainer = document.getElementById('chatContainer');
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const themeToggle = document.getElementById('themeToggle');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText'); // puede ser null si header minimal

// Landing elements
const landingScreen = document.getElementById('landingScreen');
const landingGreeting = document.getElementById('landingGreeting');
const messageInputLanding = document.getElementById('messageInputLanding');
const sendBtnLanding = document.getElementById('sendBtnLanding');
const inputArea = document.getElementById('inputArea');

// Tools menu elements
const toolsBtn = document.getElementById('toolsBtn');
const toolsMenu = document.getElementById('toolsMenu');
const toolsBtnLanding = document.getElementById('toolsBtnLanding');
const toolsMenuLanding = document.getElementById('toolsMenuLanding');
const toggleNews = document.getElementById('toggleNews');
const toggleNewsLanding = document.getElementById('toggleNewsLanding');
const newsPanel = document.getElementById('newsPanel');
const newsPanelClose = document.getElementById('newsPanelClose');
const newsPanelContent = document.getElementById('newsPanelContent');

// Model/think picker elements
const modelPickerBtn = document.getElementById('modelPickerBtn');
const modelPickerMenu = document.getElementById('modelPickerMenu');
const modelPickerBtnLanding = document.getElementById('modelPickerBtnLanding');
const modelPickerMenuLanding = document.getElementById('modelPickerMenuLanding');

// --- Configuracion ---
const API_URL = '/chat';
const HEALTH_URL = '/health';
const LLM_CATALOG_URL = '/llm/catalog';
const USER_ID = 'desktop_user';
const SOURCE = 'desktop';

// --- Estado ---
let isWaiting = false;
let pendingReleases = {}; // Cache de releases por guid
let isLandingMode = true;
let newsLoaded = false;
let modelCatalog = [];
let gptOssThinkModes = [];
let standardThinkModes = [];
let selectedModel = 'gpt-oss:20b';
let selectedThinkMode = 'medium';

const MODEL_PREFS_STORAGE_KEY = 'rufus_llm_prefs_v1';

// --- Saludos variados ---
const GREETINGS = [
    "Hey! Soy RUFÜS, tu asistente.",
    "Hola! Listo para ayudarte.",
    "Bienvenido! Preguntame lo que quieras.",
    "Qué tal! Soy RUFÜS, a tu servicio.",
    "Hey! Cuéntame, en qué te ayudo?",
    "Hola! Escríbeme lo que necesites.",
    "Buenas! Soy RUFÜS, aquí para ti.",
    "Qué onda! Pregunta lo que sea.",
    "Hey! Listo para lo que necesites.",
    "Hola! Soy RUFÜS. Dispara tu pregunta.",
];

const SOURCES_HEADER_RE = /^\s*(?:[-*]\s*)?(?:principales?\s+)?(?:fuentes?|sources?)\s*:?\s*$/i;

function getRandomGreeting() {
    return GREETINGS[Math.floor(Math.random() * GREETINGS.length)];
}

function looksLikeSourcesHeader(line) {
    return SOURCES_HEADER_RE.test((line || '').trim());
}

function toSourceUrl(value) {
    const raw = typeof value === 'string' ? value.trim() : '';
    if (!raw) return '';
    const cleaned = raw
        .replace(/^[<(\[]+/, '')
        .replace(/[>)}\]]+$/, '')
        .replace(/[),.;\]]+$/, '');
    if (/^https?:\/\//i.test(cleaned)) return cleaned;
    if (/^(?:www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:\/[^\s]*)?$/i.test(cleaned)) {
        return `https://${cleaned.replace(/^https?:\/\//i, '')}`;
    }
    return '';
}

function getSourceDomain(url) {
    try {
        return new URL(url).hostname.replace(/^www\./i, '');
    } catch {
        return '';
    }
}

function isRootSourceUrl(url) {
    try {
        const parsed = new URL(url);
        const path = parsed.pathname || '/';
        const hasPath = path && path !== '/';
        const hasQueryOrHash = Boolean(parsed.search || parsed.hash);
        return !hasPath && !hasQueryOrHash;
    } catch {
        return false;
    }
}

function normalizeSourceEntries(rawSources) {
    if (!Array.isArray(rawSources)) return [];

    const normalized = [];
    const seen = new Set();

    rawSources.forEach((entry) => {
        let urlCandidate = '';
        let labelCandidate = '';

        if (typeof entry === 'string') {
            urlCandidate = entry;
        } else if (entry && typeof entry === 'object') {
            urlCandidate =
                entry.url ||
                entry.link ||
                entry.href ||
                entry.source ||
                entry.domain ||
                '';
            labelCandidate = entry.label || entry.title || entry.name || '';
        }

        const url = toSourceUrl(String(urlCandidate || '').trim());
        if (!url) return;

        const dedupeKey = url.toLowerCase();
        if (seen.has(dedupeKey)) return;
        seen.add(dedupeKey);

        const domain = getSourceDomain(url);
        const label = String(labelCandidate || '').trim() || domain || url;
        normalized.push({ url, label, domain });
    });

    return normalized;
}

function parseSourceCandidateLine(line) {
    if (!line) return null;
    const compact = line.trim();
    if (!compact) return null;

    const candidate = compact.replace(/^[-*•●▪◦\d.)\s]+/, '');
    if (!candidate) return null;

    const markdownLink = candidate.match(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/i);
    if (markdownLink) {
        return {
            url: markdownLink[2],
            label: markdownLink[1].trim(),
        };
    }

    const urlMatch = candidate.match(/https?:\/\/[^\s)]+/i);
    if (urlMatch) {
        const url = urlMatch[0];
        const textLabel = candidate.replace(url, '').replace(/^[-:–—]\s*/, '').trim();
        return {
            url,
            label: textLabel,
        };
    }

    const domainMatch = candidate.match(/(?:www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:\/[^\s)]*)?/i);
    if (domainMatch) {
        const domainText = domainMatch[0];
        const textLabel = candidate.replace(domainText, '').replace(/^[-:–—]\s*/, '').trim();
        return {
            url: domainText,
            label: textLabel,
        };
    }

    return null;
}

function extractSourcesFromResponse(rawText) {
    const text = typeof rawText === 'string' ? rawText : String(rawText ?? '');
    if (!text.trim()) {
        return { cleanText: '', sources: [] };
    }

    const lines = text.split('\n');

    for (let i = 0; i < lines.length; i += 1) {
        if (!looksLikeSourcesHeader(lines[i])) continue;

        const candidates = [];
        let j = i + 1;
        let endIndex = j;

        while (j < lines.length) {
            const current = lines[j];
            const trimmed = current.trim();

            if (!trimmed) {
                j += 1;
                endIndex = j;
                continue;
            }

            const parsed = parseSourceCandidateLine(trimmed);
            if (!parsed) break;

            candidates.push(parsed);
            j += 1;
            endIndex = j;
        }

        const parsedSources = normalizeSourceEntries(candidates);
        if (!parsedSources.length) continue;

        const before = lines.slice(0, i);
        const after = lines.slice(endIndex);
        const cleanText = [...before, ...after]
            .join('\n')
            .replace(/\n{3,}/g, '\n\n')
            .trim();

        return { cleanText, sources: parsedSources };
    }

    return { cleanText: text.trim(), sources: [] };
}

function mergeSourceEntries(...lists) {
    const merged = [];
    const seenUrls = new Set();
    const firstRootIndexByDomain = new Map();

    lists.forEach((list) => {
        normalizeSourceEntries(list).forEach((source) => {
            const key = source.url.toLowerCase();
            if (seenUrls.has(key)) return;

            const domain = String(source.domain || getSourceDomain(source.url) || '').toLowerCase();
            const isRoot = isRootSourceUrl(source.url);

            if (domain) {
                if (isRoot) {
                    const hasSpecificAlready = merged.some(
                        (item) =>
                            String(item.domain || getSourceDomain(item.url) || '').toLowerCase() === domain
                            && !isRootSourceUrl(item.url)
                    );
                    if (hasSpecificAlready) return;
                    if (!firstRootIndexByDomain.has(domain)) {
                        firstRootIndexByDomain.set(domain, merged.length);
                    }
                } else if (firstRootIndexByDomain.has(domain)) {
                    const rootIndex = firstRootIndexByDomain.get(domain);
                    if (Number.isInteger(rootIndex) && rootIndex >= 0 && rootIndex < merged.length) {
                        const rootUrlKey = merged[rootIndex].url.toLowerCase();
                        seenUrls.delete(rootUrlKey);
                        merged.splice(rootIndex, 1);
                        firstRootIndexByDomain.delete(domain);
                        for (const [d, idx] of firstRootIndexByDomain.entries()) {
                            if (idx > rootIndex) firstRootIndexByDomain.set(d, idx - 1);
                        }
                    }
                }
            }

            seenUrls.add(key);
            merged.push(source);
        });
    });

    return merged;
}

function formatResponseTimeLabel(responseTimeMs) {
    const ms = Number(responseTimeMs);
    if (!Number.isFinite(ms) || ms < 0) return '--.--s';
    return `${(ms / 1000).toFixed(2)}s`;
}

function getMessageResponseTimeMs(messageDiv) {
    if (!messageDiv?.dataset?.responseTimeMs) return null;
    const parsed = Number(messageDiv.dataset.responseTimeMs);
    if (!Number.isFinite(parsed) || parsed < 0) return null;
    return parsed;
}

function setMessageResponseTime(messageDiv, responseTimeMs) {
    if (!messageDiv) return;
    const ms = Number(responseTimeMs);
    if (!Number.isFinite(ms) || ms < 0) {
        delete messageDiv.dataset.responseTimeMs;
        return;
    }
    messageDiv.dataset.responseTimeMs = String(ms);
}

function refreshResponseTimeAction(messageDiv) {
    if (!messageDiv) return;
    const host = messageDiv.querySelector('.agent-response-time');
    if (!host) return;
    const valueEl = host.querySelector('.agent-response-time-value');
    if (!valueEl) return;

    const responseTimeMs = getMessageResponseTimeMs(messageDiv);
    if (responseTimeMs === null) {
        host.classList.add('hidden');
        valueEl.textContent = '--.--s';
        return;
    }

    host.classList.remove('hidden');
    valueEl.textContent = formatResponseTimeLabel(responseTimeMs);
}

function normalizeModelName(modelName) {
    const normalized = String(modelName || '').trim();
    return normalized || null;
}

function normalizeThinkMode(thinkMode) {
    const normalized = String(thinkMode || '').trim().toLowerCase();
    if (!normalized) return null;
    const allowed = new Set(['low', 'medium', 'high', 'on', 'off', 'true', 'false', '1', '0']);
    return allowed.has(normalized) ? normalized : null;
}

function getModelCatalogEntry(modelName) {
    const normalizedName = String(modelName || '').trim().toLowerCase();
    if (!normalizedName || !Array.isArray(modelCatalog)) return null;
    return modelCatalog.find((entry) => String(entry?.name || '').trim().toLowerCase() === normalizedName) || null;
}

function getThinkModeTypeForModel(modelName) {
    const entry = getModelCatalogEntry(modelName);
    const declaredType = String(entry?.think_mode_type || '').trim().toLowerCase();
    if (declaredType === 'levels' || declaredType === 'toggle' || declaredType === 'none') {
        return declaredType;
    }

    const normalized = String(modelName || '').trim().toLowerCase();
    if (normalized.includes('gpt-oss')) return 'levels';
    if (normalized.startsWith('qwen3') || normalized.includes('deepseek-r1') || normalized.includes('deepseek-v3.1')) {
        return 'toggle';
    }
    return 'none';
}

function modelSupportsThinking(modelName) {
    return getThinkModeTypeForModel(modelName) !== 'none';
}

function modelSupportsThinkLevels(modelName) {
    return getThinkModeTypeForModel(modelName) === 'levels';
}

function normalizeThinkModeForModel(modelName, thinkMode) {
    const raw = normalizeThinkMode(thinkMode);
    const thinkModeType = getThinkModeTypeForModel(modelName);

    if (thinkModeType === 'levels') {
        if (raw === 'low' || raw === 'medium' || raw === 'high') return raw;
        if (raw === 'off' || raw === 'false' || raw === '0') return 'low';
        if (raw === 'on' || raw === 'true' || raw === '1') return 'medium';
        return 'medium';
    }

    if (thinkModeType === 'toggle') {
        if (raw === 'off' || raw === 'false' || raw === '0') return 'off';
        if (raw === 'on' || raw === 'true' || raw === '1') return 'on';
        if (raw === 'low' || raw === 'medium' || raw === 'high') return 'on';
        return 'on';
    }

    return null;
}

function getThinkModesForModel(modelName) {
    const thinkModeType = getThinkModeTypeForModel(modelName);
    if (thinkModeType === 'levels') {
        return gptOssThinkModes.length
            ? gptOssThinkModes
            : [
                { id: 'low', label: 'Bajo', description: 'Respuesta mas rapida.' },
                { id: 'medium', label: 'Medio', description: 'Balance velocidad/calidad.' },
                { id: 'high', label: 'Alto', description: 'Mas profundidad, mas tiempo.' },
            ];
    }

    if (thinkModeType === 'toggle') {
        return standardThinkModes.length
            ? standardThinkModes
            : [
                { id: 'off', label: 'Desactivado', description: 'Sin razonamiento extendido.' },
                { id: 'on', label: 'Activado', description: 'Con razonamiento extendido.' },
            ];
    }

    return [];
}

function getThinkModeLabel(modelName, thinkMode) {
    if (!modelSupportsThinking(modelName)) return 'No disponible';
    const normalized = normalizeThinkModeForModel(modelName, thinkMode);
    const options = getThinkModesForModel(modelName);
    const found = options.find((option) => String(option.id).toLowerCase() === normalized);
    return found ? found.label : normalized;
}

function getFriendlyModelDisplayNameFallback(modelName) {
    const raw = String(modelName || '').trim();
    const normalized = raw.toLowerCase();
    if (!raw) return 'Modelo';

    if (normalized === 'gpt-oss:20b') return 'GPT:20B';
    if (normalized.startsWith('qwen3-coder:30b')) return 'QWEN:30B';
    if (normalized.startsWith('qwen2.5-coder:14b-instruct')) return 'QWEN:14B';
    if (normalized.startsWith('mfdoom')) return 'DEEPSEEK:16B';
    if (normalized.startsWith('mistral-nemo')) return 'MISTRAL:12B';
    if (normalized.startsWith('llama3.1:8b') || normalized.startsWith('llam3.1:8b')) return 'LLAMA:8B';

    return raw;
}

function getModelDisplayName(modelName) {
    const name = String(modelName || '').trim();
    if (!name) return 'Modelo';

    const entry = getModelCatalogEntry(name);
    const catalogDisplay = String(entry?.display_name || '').trim();
    if (catalogDisplay) return catalogDisplay;

    return getFriendlyModelDisplayNameFallback(name);
}

function saveModelPreferences() {
    const payload = {
        model: selectedModel,
        thinkMode: selectedThinkMode,
    };
    localStorage.setItem(MODEL_PREFS_STORAGE_KEY, JSON.stringify(payload));
}

function loadModelPreferences() {
    try {
        const raw = localStorage.getItem(MODEL_PREFS_STORAGE_KEY);
        if (!raw) return;
        const parsed = JSON.parse(raw);
        const model = normalizeModelName(parsed?.model);
        const thinkMode = normalizeThinkMode(parsed?.thinkMode);
        if (model) selectedModel = model;
        if (thinkMode) selectedThinkMode = thinkMode;
    } catch (error) {
        console.warn('No se pudieron cargar preferencias de modelo:', error);
    }
}

function getCatalogModelNames() {
    return modelCatalog.map((item) => String(item?.name || '').trim()).filter(Boolean);
}

function ensureModelSelectionIsValid() {
    const catalogNames = getCatalogModelNames();
    if (catalogNames.length === 0) {
        selectedModel = normalizeModelName(selectedModel) || 'gpt-oss:20b';
    } else if (!catalogNames.includes(selectedModel)) {
        selectedModel = catalogNames[0];
    }
    selectedThinkMode = normalizeThinkModeForModel(selectedModel, selectedThinkMode);
}

function updateModelPickerButtons() {
    const main = getModelDisplayName(selectedModel);
    const buttons = [modelPickerBtn, modelPickerBtnLanding];

    buttons.forEach((button) => {
        if (!button) return;
        const mainEl = button.querySelector('.model-picker-btn-main');
        if (mainEl) mainEl.textContent = main;
        const thinkLabel = getThinkModeLabel(selectedModel, selectedThinkMode);
        button.title = modelSupportsThinking(selectedModel)
            ? `${main} · Pensamiento ${thinkLabel}`
            : `${main} · Sin pensamiento extendido`;
        button.setAttribute('aria-label', button.title);
    });
}

function renderModelPickerMenu(menu) {
    if (!menu) return;
    menu.innerHTML = '';

    if (modelSupportsThinking(selectedModel)) {
        const thinkSection = document.createElement('section');
        thinkSection.className = 'model-picker-section';
        const thinkTitle = document.createElement('div');
        thinkTitle.className = 'model-picker-section-title';
        thinkTitle.textContent = 'Pensamiento';
        thinkSection.appendChild(thinkTitle);

        const thinkOptions = getThinkModesForModel(selectedModel);
        const normalizedThink = normalizeThinkModeForModel(selectedModel, selectedThinkMode);
        thinkOptions.forEach((option) => {
            const optionId = normalizeThinkModeForModel(selectedModel, option.id);
            const item = document.createElement('button');
            item.type = 'button';
            item.className = `model-picker-option${optionId === normalizedThink ? ' active' : ''}`;
            item.innerHTML = `
                <span class="model-picker-option-main">
                    <span>${escapeHtml(option.label || option.id)}</span>
                    ${optionId === normalizedThink ? '<span class="model-picker-check">OK</span>' : ''}
                </span>
                <span class="model-picker-option-desc">${escapeHtml(option.description || '')}</span>
            `;
            item.addEventListener('click', (event) => {
                event.stopPropagation();
                selectedThinkMode = optionId;
                saveModelPreferences();
                updateModelPickerButtons();
                renderModelPickerMenus();
            });
            thinkSection.appendChild(item);
        });
        menu.appendChild(thinkSection);
    }

    const modelSection = document.createElement('section');
    modelSection.className = 'model-picker-section';
    const modelTitle = document.createElement('div');
    modelTitle.className = 'model-picker-section-title';
    modelTitle.textContent = 'Modelos';
    modelSection.appendChild(modelTitle);

    const models = modelCatalog.length
        ? [...modelCatalog]
        : [{ name: selectedModel, supports_think_levels: modelSupportsThinkLevels(selectedModel) }];

    models.forEach((modelItem) => {
        const name = normalizeModelName(modelItem?.name);
        if (!name) return;

        const isActive = name === selectedModel;
        const item = document.createElement('button');
        item.type = 'button';
        item.className = `model-picker-option${isActive ? ' active' : ''}`;
        item.innerHTML = `
            <span class="model-picker-option-main">
                <span>${escapeHtml(getModelDisplayName(name))}</span>
                ${isActive ? '<span class="model-picker-check">OK</span>' : ''}
            </span>
        `;
        item.addEventListener('click', (event) => {
            event.stopPropagation();
            selectedModel = name;
            selectedThinkMode = normalizeThinkModeForModel(selectedModel, selectedThinkMode);
            saveModelPreferences();
            updateModelPickerButtons();
            renderModelPickerMenus();
        });
        modelSection.appendChild(item);
    });
    menu.appendChild(modelSection);
}

function renderModelPickerMenus() {
    renderModelPickerMenu(modelPickerMenu);
    renderModelPickerMenu(modelPickerMenuLanding);
}

function closeAllModelMenus() {
    document.querySelectorAll('.model-picker-menu').forEach((menu) => menu.classList.remove('visible'));
    document.querySelectorAll('.model-picker-btn').forEach((btn) => btn.classList.remove('active'));
}

function toggleModelPicker(btn, menu) {
    if (!btn || !menu) return;
    const isVisible = menu.classList.contains('visible');

    closeAllModelMenus();
    closeAllToolsMenus();
    closeAllSourceMenus();

    if (!isVisible) {
        renderModelPickerMenus();
        menu.classList.add('visible');
        btn.classList.add('active');
    }
}

async function loadLLMCatalog() {
    try {
        const response = await fetch(LLM_CATALOG_URL);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const payload = await response.json();
        const incomingModels = Array.isArray(payload.models) ? payload.models : [];
        modelCatalog = incomingModels
            .map((entry) => ({
                name: normalizeModelName(entry?.name),
                display_name: String(entry?.display_name || '').trim() || null,
                supports_thinking: Boolean(entry?.supports_thinking),
                supports_think_levels: Boolean(entry?.supports_think_levels),
                think_mode_type: ['levels', 'toggle', 'none'].includes(String(entry?.think_mode_type || '').toLowerCase())
                    ? String(entry?.think_mode_type || '').toLowerCase()
                    : (Boolean(entry?.supports_think_levels) ? 'levels' : (Boolean(entry?.supports_thinking) ? 'toggle' : 'none')),
            }))
            .filter((entry) => entry.name);

        gptOssThinkModes = Array.isArray(payload.gpt_oss_think_modes)
            ? payload.gpt_oss_think_modes
            : [];
        standardThinkModes = Array.isArray(payload.standard_think_modes)
            ? payload.standard_think_modes
            : [];

        const currentModel = normalizeModelName(payload.current_model);
        const currentThinkMode = normalizeThinkMode(payload.current_think_mode);

        if (!localStorage.getItem(MODEL_PREFS_STORAGE_KEY)) {
            if (currentModel) selectedModel = currentModel;
            if (currentThinkMode) selectedThinkMode = currentThinkMode;
        } else if (!selectedModel && currentModel) {
            selectedModel = currentModel;
        }
    } catch (error) {
        console.warn('No se pudo cargar catalogo de modelos, usando valores locales.', error);
        modelCatalog = [];
    }

    ensureModelSelectionIsValid();
    saveModelPreferences();
    updateModelPickerButtons();
    renderModelPickerMenus();
}

// ==========================================
// FUNCIONES PRINCIPALES
// ==========================================

/**
 * Envia un mensaje al backend y muestra la respuesta
 */
function setWaitingState(waiting) {
    isWaiting = waiting;
    sendBtn.disabled = waiting;
    if (sendBtnLanding) sendBtnLanding.disabled = waiting;
    updateRegenerateButtonsDisabledState();
}

async function fetchChatReply(userText) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 150000); // 2.5 min timeout
    const resolvedModel = normalizeModelName(selectedModel) || 'gpt-oss:20b';
    const resolvedThinkMode = normalizeThinkModeForModel(resolvedModel, selectedThinkMode);
    const payload = {
        message: userText,
        user_id: USER_ID,
        source: SOURCE,
        model: resolvedModel,
    };
    if (resolvedThinkMode) {
        payload.think_mode = resolvedThinkMode;
    }

    try {
        const response = await fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: controller.signal
        });

        if (!response.ok) {
            throw new Error(`Error HTTP: ${response.status}`);
        }

        return await response.json();
    } finally {
        clearTimeout(timeoutId);
    }
}

async function runAssistantTurn(userText, options = {}) {
    const prompt = (userText || '').trim();
    const appendUserMessage = options.appendUserMessage !== false;
    const replaceAgentMessage = options.replaceAgentMessage || null;
    if (!prompt || isWaiting) return;

    if (appendUserMessage) {
        await appendMessage(prompt, 'user');
    }

    const typingEl = showTypingIndicator();
    setWaitingState(true);

    try {
        const requestStart = performance.now();
        const data = await fetchChatReply(prompt);
        const responseTimeMs = Math.max(0, performance.now() - requestStart);
        const backendModel = normalizeModelName(data?.model_used);
        const backendThink = normalizeThinkMode(data?.think_mode_used);
        if (backendModel) {
            selectedModel = backendModel;
            if (backendThink) {
                selectedThinkMode = normalizeThinkModeForModel(backendModel, backendThink);
            } else {
                selectedThinkMode = normalizeThinkModeForModel(backendModel, selectedThinkMode);
            }
            saveModelPreferences();
            updateModelPickerButtons();
            renderModelPickerMenus();
        }
        removeTypingIndicator(typingEl);

        if (data.movie) {
            if (replaceAgentMessage) {
                const fallbackMessage =
                    typeof data.response === 'string' && data.response.trim()
                        ? data.response
                        : 'La regeneracion devolvio una tarjeta de pelicula en lugar de texto.';
                const extracted = extractSourcesFromResponse(fallbackMessage);
                const mergedSources = mergeSourceEntries(data.sources, extracted.sources);
                await setAgentMessageContent(replaceAgentMessage, extracted.cleanText, prompt, {
                    sources: mergedSources,
                    responseTimeMs,
                });
            } else {
                appendMovieCard(data.movie);
            }
            return;
        }

        const rawAgentText = typeof data.response === 'string' ? data.response : '';
        const extracted = extractSourcesFromResponse(rawAgentText);
        const mergedSources = mergeSourceEntries(data.sources, extracted.sources);
        const agentText = extracted.cleanText;
        if (replaceAgentMessage) {
            await setAgentMessageContent(replaceAgentMessage, agentText, prompt, {
                sources: mergedSources,
                responseTimeMs,
            });
        } else {
            await appendMessage(agentText, 'agent', {
                userPrompt: prompt,
                sources: mergedSources,
                responseTimeMs,
            });
        }
    } catch (error) {
        console.error('Error al enviar mensaje:', error);
        removeTypingIndicator(typingEl);
        const errorMsg = error.name === 'AbortError'
            ? 'La respuesta tardo demasiado. El modelo puede estar sobrecargado, intenta de nuevo.'
            : 'No pude conectarme al servidor. Verifica que este corriendo en localhost:8000.';

        if (replaceAgentMessage) {
            await setAgentMessageContent(replaceAgentMessage, errorMsg, prompt, { sources: [] });
        } else {
            await appendMessage(errorMsg, 'agent', { userPrompt: prompt, sources: [] });
        }
    } finally {
        setWaitingState(false);
        messageInput.focus();
    }
}

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text || isWaiting) return;

    // Limpiar input
    messageInput.value = '';
    autoResizeTextarea();
    updateSendButton();

    await runAssistantTurn(text, { appendUserMessage: true });
}

function getMessageSources(messageDiv) {
    if (!messageDiv?.dataset?.sources) return [];
    try {
        const parsed = JSON.parse(messageDiv.dataset.sources);
        return normalizeSourceEntries(parsed);
    } catch {
        return [];
    }
}

function setMessageSources(messageDiv, sources) {
    if (!messageDiv) return;
    const normalized = normalizeSourceEntries(sources);
    if (!normalized.length) {
        delete messageDiv.dataset.sources;
        return;
    }
    messageDiv.dataset.sources = JSON.stringify(normalized);
}

/**
 * Agrega un mensaje al area de chat
 */
async function appendMessage(text, role, options = {}) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;

    const bubbleDiv = document.createElement('div');
    bubbleDiv.className = 'bubble';

    if (role === 'agent') {
        const agentText = typeof text === 'string' ? text : String(text ?? '');
        bubbleDiv.classList.add('markdown');
        messageDiv.dataset.rawText = agentText;
        messageDiv.dataset.userPrompt = typeof options.userPrompt === 'string'
            ? options.userPrompt.trim()
            : '';
        setMessageSources(messageDiv, options.sources);
        setMessageResponseTime(messageDiv, options.responseTimeMs);
        messageDiv.appendChild(bubbleDiv);
        const actionsDiv = createAgentActions(messageDiv);
        actionsDiv.classList.add('pending');
        messageDiv.appendChild(actionsDiv);
    } else {
        const userText = typeof text === 'string' ? text : String(text ?? '');
        messageDiv.dataset.rawText = userText;
        // Mostrar texto del usuario literal para conservar su formato.
        bubbleDiv.textContent = userText;
        messageDiv.appendChild(bubbleDiv);
        const actionsDiv = createUserActions(messageDiv);
        messageDiv.appendChild(actionsDiv);
    }

    chatMessages.appendChild(messageDiv);
    if (role === 'agent') {
        const agentText = messageDiv.dataset.rawText || '';
        await renderAgentMessage(messageDiv, bubbleDiv, agentText, {
            animateTyping: options.animateTyping !== false,
        });
    } else {
        updateRegenerateButtonsDisabledState();
        scrollToBottom();
    }
    return messageDiv;
}

async function setAgentMessageContent(messageDiv, text, userPrompt = '', options = {}) {
    if (!messageDiv) return;
    const bubbleDiv = messageDiv.querySelector('.bubble');
    if (!bubbleDiv) return;

    const normalizedText = typeof text === 'string' ? text : String(text ?? '');
    messageDiv.dataset.rawText = normalizedText;
    if (typeof userPrompt === 'string') {
        messageDiv.dataset.userPrompt = userPrompt.trim();
    }
    if (Object.prototype.hasOwnProperty.call(options, 'sources')) {
        setMessageSources(messageDiv, options.sources);
    }
    if (Object.prototype.hasOwnProperty.call(options, 'responseTimeMs')) {
        setMessageResponseTime(messageDiv, options.responseTimeMs);
    }
    await renderAgentMessage(messageDiv, bubbleDiv, normalizedText, {
        animateTyping: options.animateTyping !== false,
    });
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function animateAgentText(bubbleDiv, fullText) {
    const text = typeof fullText === 'string' ? fullText : String(fullText ?? '');
    if (!text) {
        bubbleDiv.innerHTML = '';
        return;
    }

    const minDurationMs = 280;
    const maxDurationMs = 2800;
    const estimatedDurationMs = Math.min(maxDurationMs, Math.max(minDurationMs, text.length * 17));
    const stepIntervalMs = 24;
    const steps = Math.max(8, Math.round(estimatedDurationMs / stepIntervalMs));
    const charsPerStep = Math.max(1, Math.ceil(text.length / steps));

    let index = 0;
    while (index < text.length) {
        index = Math.min(text.length, index + charsPerStep);
        bubbleDiv.innerHTML = formatText(text.slice(0, index));
        scrollToBottom();
        await sleep(stepIntervalMs);
    }
}

async function renderAgentMessage(messageDiv, bubbleDiv, text, options = {}) {
    const actionsDiv = messageDiv.querySelector('.agent-actions');
    if (actionsDiv) actionsDiv.classList.add('pending');

    if (options.animateTyping) {
        await animateAgentText(bubbleDiv, text);
    } else {
        bubbleDiv.innerHTML = formatText(text);
    }

    if (actionsDiv) actionsDiv.classList.remove('pending');
    refreshSourceActionState(messageDiv);
    refreshResponseTimeAction(messageDiv);
    updateRegenerateButtonsDisabledState();
    scrollToBottom();
}

function getAgentActionIconMarkup(variant, state = 'default') {
    if (variant === 'copy') {
        if (state === 'success') {
            return `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M20 7L9 18l-5-5"></path>
                </svg>
            `;
        }
        if (state === 'error') {
            return `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="9"></circle>
                    <line x1="12" y1="8" x2="12" y2="13"></line>
                    <circle cx="12" cy="17" r="1"></circle>
                </svg>
            `;
        }
        return `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <rect x="9" y="9" width="11" height="11" rx="2"></rect>
                <path d="M5 15V5a2 2 0 0 1 2-2h10"></path>
            </svg>
        `;
    }

    if (variant === 'regenerate') {
        return `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M21 12a9 9 0 1 1-2.64-6.36"></path>
                <polyline points="21 3 21 9 15 9"></polyline>
            </svg>
        `;
    }

    if (variant === 'sources') {
        return `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.05" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M6 4h8a2 2 0 0 1 2 2v12H8a2 2 0 0 0-2 2"></path>
                <path d="M8 20h10"></path>
                <path d="M6 4v16"></path>
            </svg>
        `;
    }

    return '';
}

function setActionButtonIcon(button, variant, state = 'default') {
    if (!button) return;
    button.innerHTML = `<span class="agent-action-icon${state === 'loading' ? ' spinning' : ''}">${getAgentActionIconMarkup(variant, state)}</span>`;
}

function createAgentActionButton(label, variant) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `agent-action-btn ${variant}`;
    btn.title = label;
    btn.setAttribute('aria-label', label);
    btn.dataset.variant = variant;
    setActionButtonIcon(btn, variant, 'default');
    return btn;
}

function setTemporaryCopyState(button, state, timeoutMs = 1400) {
    if (!button) return;
    if (button.dataset.restoreTimeoutId) {
        clearTimeout(Number(button.dataset.restoreTimeoutId));
    }

    setActionButtonIcon(button, 'copy', state);
    if (state === 'success') {
        button.classList.add('success');
    } else if (state === 'error') {
        button.classList.add('error');
    }

    const timeoutId = setTimeout(() => {
        setActionButtonIcon(button, 'copy', 'default');
        button.classList.remove('success');
        button.classList.remove('error');
        delete button.dataset.restoreTimeoutId;
    }, timeoutMs);
    button.dataset.restoreTimeoutId = String(timeoutId);
}

function closeAllSourceMenus() {
    document.querySelectorAll('.sources-menu').forEach((menu) => menu.classList.remove('visible'));
    document.querySelectorAll('.agent-action-btn.sources').forEach((btn) => btn.classList.remove('active'));
}

function renderSourcesMenu(menuEl, sources) {
    if (!menuEl) return;
    menuEl.innerHTML = '';

    const title = document.createElement('div');
    title.className = 'sources-menu-title';
    title.textContent = `${sources.length} fuente${sources.length === 1 ? '' : 's'}`;
    menuEl.appendChild(title);

    const list = document.createElement('div');
    list.className = 'sources-menu-list';

    sources.forEach((source) => {
        const link = document.createElement('a');
        link.className = 'sources-menu-link';
        link.href = source.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = source.label || source.domain || source.url;
        list.appendChild(link);
    });

    menuEl.appendChild(list);
}

function refreshSourceActionState(messageDiv) {
    if (!messageDiv) return;
    const sourceAction = messageDiv.querySelector('.source-action');
    if (!sourceAction) return;

    const sourceBtn = sourceAction.querySelector('.agent-action-btn.sources');
    const sourceMenu = sourceAction.querySelector('.sources-menu');
    const sources = getMessageSources(messageDiv);

    if (!sources.length) {
        sourceAction.classList.add('hidden');
        if (sourceBtn) {
            sourceBtn.disabled = true;
            sourceBtn.dataset.count = '0';
        }
        if (sourceMenu) sourceMenu.classList.remove('visible');
        return;
    }

    sourceAction.classList.remove('hidden');
    if (sourceBtn) {
        sourceBtn.disabled = false;
        sourceBtn.dataset.count = String(sources.length);
        sourceBtn.title = `Fuentes (${sources.length})`;
        sourceBtn.setAttribute('aria-label', `Fuentes (${sources.length})`);
    }
    renderSourcesMenu(sourceMenu, sources);
}

function toggleSourceMenu(messageDiv, sourceAction, sourceBtn, sourceMenu) {
    if (!sourceAction || !sourceBtn || !sourceMenu) return;
    const isOpen = sourceMenu.classList.contains('visible');
    closeAllSourceMenus();
    if (isOpen) return;

    refreshSourceActionState(messageDiv);
    if (sourceAction.classList.contains('hidden')) return;

    sourceBtn.classList.add('active');
    sourceMenu.classList.add('visible');
}

function copyTextFallback(text) {
    const temp = document.createElement('textarea');
    temp.value = text;
    temp.setAttribute('readonly', 'true');
    temp.style.position = 'fixed';
    temp.style.opacity = '0';
    document.body.appendChild(temp);
    temp.select();
    document.execCommand('copy');
    temp.remove();
}

async function copyAgentMessage(messageDiv, button) {
    const text = (messageDiv.dataset.rawText || messageDiv.querySelector('.bubble')?.textContent || '').trim();
    if (!text) return;

    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
        } else {
            copyTextFallback(text);
        }
        setTemporaryCopyState(button, 'success');
    } catch (error) {
        console.error('No se pudo copiar el mensaje:', error);
        setTemporaryCopyState(button, 'error', 1200);
    }
}

async function copyUserMessage(messageDiv, button) {
    const text = (messageDiv.dataset.rawText || messageDiv.querySelector('.bubble')?.textContent || '').trim();
    if (!text) return;

    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
        } else {
            copyTextFallback(text);
        }
        setTemporaryCopyState(button, 'success');
    } catch (error) {
        console.error('No se pudo copiar el mensaje del usuario:', error);
        setTemporaryCopyState(button, 'error', 1200);
    }
}

async function regenerateAgentMessage(messageDiv, button) {
    const prompt = (messageDiv.dataset.userPrompt || '').trim();
    if (!prompt || isWaiting) return;

    setActionButtonIcon(button, 'regenerate', 'loading');
    button.classList.add('loading');
    button.disabled = true;

    try {
        await runAssistantTurn(prompt, {
            appendUserMessage: false,
            replaceAgentMessage: messageDiv,
        });
    } finally {
        button.classList.remove('loading');
        setActionButtonIcon(button, 'regenerate', 'default');
        updateRegenerateButtonsDisabledState();
    }
}

function createAgentActions(messageDiv) {
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'agent-actions';

    const sourceAction = document.createElement('div');
    sourceAction.className = 'source-action';
    const sourceBtn = createAgentActionButton('Fuentes', 'sources');
    sourceBtn.classList.add('sources');
    const sourceMenu = document.createElement('div');
    sourceMenu.className = 'sources-menu';
    sourceMenu.addEventListener('click', (e) => e.stopPropagation());
    sourceBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleSourceMenu(messageDiv, sourceAction, sourceBtn, sourceMenu);
    });
    sourceAction.appendChild(sourceBtn);
    sourceAction.appendChild(sourceMenu);
    actionsDiv.appendChild(sourceAction);

    const copyBtn = createAgentActionButton('Copiar', 'copy');
    copyBtn.addEventListener('click', () => copyAgentMessage(messageDiv, copyBtn));

    const regenerateBtn = createAgentActionButton('Regenerar', 'regenerate');
    regenerateBtn.addEventListener('click', () => regenerateAgentMessage(messageDiv, regenerateBtn));

    actionsDiv.appendChild(copyBtn);
    actionsDiv.appendChild(regenerateBtn);

    const responseTimeEl = document.createElement('div');
    responseTimeEl.className = 'agent-response-time';
    responseTimeEl.innerHTML = `
        <span class="agent-response-time-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="9"></circle>
                <path d="M12 7v5l3 2"></path>
            </svg>
        </span>
        <span class="agent-response-time-value">--.--s</span>
    `;
    actionsDiv.appendChild(responseTimeEl);

    refreshSourceActionState(messageDiv);
    refreshResponseTimeAction(messageDiv);
    return actionsDiv;
}

function createUserActions(messageDiv) {
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'user-actions';

    const copyBtn = createAgentActionButton('Copiar', 'copy');
    copyBtn.classList.add('user-copy-btn');
    copyBtn.addEventListener('click', () => copyUserMessage(messageDiv, copyBtn));

    actionsDiv.appendChild(copyBtn);
    return actionsDiv;
}

function updateRegenerateButtonsDisabledState() {
    chatMessages.querySelectorAll('.agent-action-btn.regenerate').forEach((btn) => {
        const hostMessage = btn.closest('.message.agent');
        const userPrompt = (hostMessage?.dataset.userPrompt || '').trim();
        btn.disabled = isWaiting || !userPrompt;
    });
}

/**
 * Muestra el indicador de "pensando..."
 */
function showTypingIndicator() {
    const typingDiv = document.createElement('div');
    typingDiv.className = 'typing-indicator';
    typingDiv.innerHTML = `
        <div class="thinking-shell" aria-label="RUFUS pensando">
            <div class="fluid-cloud" aria-hidden="true"></div>
        </div>
    `;

    const fluidCloud = typingDiv.querySelector('.fluid-cloud');
    if (fluidCloud) {
        const particles = 20;
        const anchors = [
            { x: 0.22, y: 0.42 },
            { x: 0.42, y: 0.28 },
            { x: 0.7, y: 0.58 },
            { x: 0.52, y: 0.72 },
        ];
        for (let i = 0; i < particles; i += 1) {
            const particle = document.createElement('span');
            particle.className = 'fluid-particle';
            const anchor = anchors[i % anchors.length];
            const anchorX = Math.max(0.1, Math.min(0.9, anchor.x + (Math.random() * 0.16 - 0.08)));
            const anchorY = Math.max(0.12, Math.min(0.88, anchor.y + (Math.random() * 0.14 - 0.07)));

            particle.style.left = `${(anchorX * 100).toFixed(2)}%`;
            particle.style.top = `${(anchorY * 100).toFixed(2)}%`;
            particle.style.setProperty('--ax', `${(2.4 + Math.random() * 6.2).toFixed(2)}px`);
            particle.style.setProperty('--ay', `${(2 + Math.random() * 5.4).toFixed(2)}px`);
            particle.style.setProperty('--jx', `${(Math.random() * 2 - 1).toFixed(3)}`);
            particle.style.setProperty('--jy', `${(Math.random() * 2 - 1).toFixed(3)}`);
            particle.style.setProperty('--driftx', `${(Math.random() * 1.8 - 0.9).toFixed(2)}px`);
            particle.style.setProperty('--drifty', `${(Math.random() * 1.8 - 0.9).toFixed(2)}px`);
            particle.style.setProperty('--size', `${(1.7 + Math.random() * 2.6).toFixed(2)}px`);
            particle.style.setProperty('--dur', `${(4.1 + Math.random() * 3.6).toFixed(2)}s`);
            particle.style.setProperty('--delay', `${(Math.random() * 1.8).toFixed(2)}s`);
            particle.style.setProperty('--alpha', `${(0.24 + Math.random() * 0.36).toFixed(2)}`);
            fluidCloud.appendChild(particle);
        }

        const handlePointerMove = (event) => {
            const rect = fluidCloud.getBoundingClientRect();
            if (!rect.width || !rect.height) return;
            const nx = ((event.clientX - rect.left) / rect.width) - 0.5;
            const ny = ((event.clientY - rect.top) / rect.height) - 0.5;
            const clampedX = Math.max(-0.35, Math.min(0.35, nx));
            const clampedY = Math.max(-0.35, Math.min(0.35, ny));
            fluidCloud.style.setProperty('--cursor-x', clampedX.toFixed(3));
            fluidCloud.style.setProperty('--cursor-y', clampedY.toFixed(3));
        };

        const handlePointerLeave = () => {
            fluidCloud.style.setProperty('--cursor-x', '0');
            fluidCloud.style.setProperty('--cursor-y', '0');
        };

        typingDiv.addEventListener('pointermove', handlePointerMove);
        typingDiv.addEventListener('pointerleave', handlePointerLeave);
    }

    chatMessages.appendChild(typingDiv);
    scrollToBottom();
    return typingDiv;
}

/**
 * Quita el indicador de escribiendo
 */
function removeTypingIndicator(element) {
    if (element && element.parentNode) {
        element.style.opacity = '0';
        element.style.transform = 'translateY(-4px)';
        element.style.transition = 'all 0.2s ease';
        setTimeout(() => element.remove(), 200);
    }
}

/**
 * Scroll automatico al fondo del chat
 */
function scrollToBottom() {
    requestAnimationFrame(() => {
        chatContainer.scrollTo({
            top: chatContainer.scrollHeight,
            behavior: 'smooth'
        });
    });
}

/**
 * Escapa caracteres HTML para evitar inyecciones al renderizar markdown.
 */
function escapeHtml(text) {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

/**
 * Aplica estilos inline de markdown sobre texto ya escapado.
 */
function formatInline(text) {
    let formatted = text;

    // Permitir saltos de linea HTML comunes del modelo (<br>, <br/>) de forma controlada.
    formatted = formatted.replace(/&lt;br\s*\/?&gt;/gi, '<br>');
    // Tambien soportar secuencias literales "\n" cuando el modelo las devuelve en texto.
    formatted = formatted.replace(/\\n/g, '<br>');

    // Links [texto](https://url)
    formatted = formatted.replace(
        /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    );

    // Codigo inline (`code`)
    formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Subrayado estilo ++texto++
    formatted = formatted.replace(/\+\+([^+\n][^+\n]*?)\+\+/g, '<u>$1</u>');

    // Negritas (**texto** o __texto__)
    formatted = formatted.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    formatted = formatted.replace(/__([^_]+)__/g, '<strong>$1</strong>');

    // Cursiva (*texto* o _texto_)
    formatted = formatted.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
    formatted = formatted.replace(/_([^_\n]+)_/g, '<em>$1</em>');

    // Tachado (~~texto~~)
    formatted = formatted.replace(/~~([^~]+)~~/g, '<del>$1</del>');

    // Subrayado HTML permitido (escapado previamente)
    formatted = formatted.replace(/&lt;u&gt;([\s\S]*?)&lt;\/u&gt;/gi, '<u>$1</u>');

    return formatted;
}

function isTableSeparator(line) {
    const normalized = (line || '').replace(/[—–]/g, '-');
    return /^\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?$/.test(normalized);
}

function parseTableRow(line) {
    const normalized = (line || '').replace(/｜/g, '|');
    const cleanLine = normalized.trim().replace(/^\|/, '').replace(/\|$/, '');
    return cleanLine.split('|').map((cell) => formatInline(cell.trim()));
}

function normalizeMarkdownInput(rawText) {
    const normalizedNewlines = (rawText || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    return normalizedNewlines
        .replace(/｜/g, '|')
        .split('\n')
        .map((line) => {
            let cleaned = line;
            cleaned = cleaned.replace(/^\s*[•●▪◦]\s+/, '- ');
            cleaned = cleaned.replace(/^(\s*[-*+]\s+.*)\s+\|\s*$/, '$1');
            return cleaned;
        })
        .join('\n');
}

function cleanLooseTableArtifacts(line) {
    let cleaned = line || '';
    if (/^\|?\s*[:\-—–]{3,}(?:\s*\|\s*[:\-—–]{3,})+\s*\|?$/.test(cleaned)) {
        return '';
    }
    if (/^\|.*\|$/.test(cleaned)) {
        cleaned = cleaned.replace(/^\|/, '').replace(/\|$/, '').trim();
    }
    cleaned = cleaned.replace(/\s+\|\s*$/, '');
    return cleaned;
}

function renderComparisonCards(rows, priceLabel = 'Precio') {
    if (!rows || rows.length === 0) return '';
    const cards = rows
        .map((row) => {
            const bulletsHtml =
                row.bullets && row.bullets.length
                    ? `<ul class="comparison-bullets">${row.bullets.map((item) => `<li>${item}</li>`).join('')}</ul>`
                    : '';
            return `
                <article class="comparison-card">
                    <div class="comparison-card-header">
                        <h4>${row.title}</h4>
                        <p><span>${priceLabel}:</span> ${row.price}</p>
                    </div>
                    ${bulletsHtml}
                </article>
            `;
        })
        .join('');

    return `<section class="comparison-grid">${cards}</section>`;
}

function isLikelyMarkdownTableBlock(blockText) {
    const lines = (blockText || '')
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
    if (lines.length < 2) return false;

    for (let i = 0; i < lines.length - 1; i += 1) {
        if (lines[i].includes('|') && isTableSeparator(lines[i + 1])) {
            return true;
        }
    }
    return false;
}

function renderMarkdownTableFromBlock(blockText) {
    const lines = (blockText || '')
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
    if (lines.length < 2) return null;

    let headerIndex = -1;
    for (let i = 0; i < lines.length - 1; i += 1) {
        if (lines[i].includes('|') && isTableSeparator(lines[i + 1])) {
            headerIndex = i;
            break;
        }
    }

    if (headerIndex < 0) return null;

    const headers = parseTableRow(lines[headerIndex]);
    const rows = [];
    for (let i = headerIndex + 2; i < lines.length; i += 1) {
        const rowLine = lines[i];
        if (!rowLine || isTableSeparator(rowLine)) continue;
        if (!rowLine.includes('|')) break;
        rows.push(parseTableRow(rowLine));
    }

    if (headers.length < 2) return null;

    const thead = `<thead><tr>${headers.map((cell) => `<th>${cell}</th>`).join('')}</tr></thead>`;
    const tbody =
        rows.length > 0
            ? `<tbody>${rows
                  .map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join('')}</tr>`)
                  .join('')}</tbody>`
            : '';

    return `<div class="table-wrap"><table class="minimal-table">${thead}${tbody}</table></div>`;
}

/**
 * Renderiza un subconjunto seguro de HTML cuando el modelo responde con tags.
 * Permitimos solo etiquetas estructurales comunes del chat.
 */
function renderAllowedHtmlSubset(rawText) {
    if (!rawText) return null;

    const hasAllowedTag = /<\/?(table|thead|tbody|tr|th|td|br|ul|ol|li|p|strong|em|b|i|u)\b/i.test(rawText);
    if (!hasAllowedTag) return null;

    // Escapar todo primero, luego "des-escapar" solo etiquetas permitidas sin atributos.
    let html = escapeHtml(rawText);

    const allowSimpleTag = (tag) => {
        const openRe = new RegExp(`&lt;\\s*${tag}(?:\\s+[^&]*)&gt;`, 'gi');
        const closeRe = new RegExp(`&lt;\\s*\\/\\s*${tag}\\s*&gt;`, 'gi');
        html = html.replace(openRe, `<${tag}>`);
        html = html.replace(closeRe, `</${tag}>`);
    };

    [
        'table',
        'thead',
        'tbody',
        'tr',
        'th',
        'td',
        'ul',
        'ol',
        'li',
        'p',
        'strong',
        'em',
        'b',
        'i',
        'u',
    ].forEach(allowSimpleTag);

    html = html.replace(/&lt;\s*br\s*\/?\s*&gt;/gi, '<br>');

    // Si no queda ninguna etiqueta permitida real, no usar este modo.
    if (!/<(table|thead|tbody|tr|th|td|br|ul|ol|li|p|strong|em|b|i|u)\b/i.test(html)) {
        return null;
    }

    // En modo HTML, mantenemos saltos fuera de tags para evitar bloques pegados.
    return html.replace(/\n/g, '<br>');
}

/**
 * Renderiza markdown ligero a HTML con bloques (headings, listas, tabla, citas).
 */
function formatText(text) {
    const normalizedText = normalizeMarkdownInput(text);
    const htmlSubset = renderAllowedHtmlSubset(normalizedText);
    if (htmlSubset) {
        return htmlSubset;
    }

    const safeText = escapeHtml(normalizedText);
    const codeBlocks = [];

    // Sustituimos bloques de codigo por tokens para no parsearlos como markdown normal.
    const withTokens = safeText.replace(/```(\w*)\n?([\s\S]*?)```/g, (match, lang, code) => {
        const codeText = (code || '').trim();
        if (isLikelyMarkdownTableBlock(codeText)) {
            const tableHtml = renderMarkdownTableFromBlock(codeText);
            if (tableHtml) {
                const token = `@@CODEBLOCK_${codeBlocks.length}@@`;
                codeBlocks.push(tableHtml);
                return token;
            }
        }

        const language = (lang || '').trim();
        const languageClass = language ? ` class="language-${language}"` : '';
        const token = `@@CODEBLOCK_${codeBlocks.length}@@`;
        codeBlocks.push(`<pre><code${languageClass}>${codeText}</code></pre>`);
        return token;
    });

    const lines = withTokens.split('\n');
    const html = [];
    let i = 0;
    let listType = null; // "ul" | "ol" | null
    let listItems = [];
    let orderedItemNumbers = [];
    let orderedSequenceCounter = 0;

    const resetOrderedSequence = () => {
        orderedSequenceCounter = 0;
    };

    const flushList = () => {
        if (!listType || listItems.length === 0) return;
        if (listType === 'ol') {
            const firstProvided = Number.isFinite(orderedItemNumbers[0]) ? orderedItemNumbers[0] : 1;
            const startNumber =
                firstProvided === 1 && orderedSequenceCounter > 0
                    ? orderedSequenceCounter + 1
                    : Math.max(1, firstProvided);
            const startAttr = startNumber > 1 ? ` start="${startNumber}"` : '';
            html.push(`<ol class="md-list"${startAttr}>${listItems.map((item) => `<li>${item}</li>`).join('')}</ol>`);
            orderedSequenceCounter = startNumber + listItems.length - 1;
        } else {
            html.push(`<ul class="md-list">${listItems.map((item) => `<li>${item}</li>`).join('')}</ul>`);
        }
        listType = null;
        listItems = [];
        orderedItemNumbers = [];
    };

    const isLikelyTitleLine = (value) => {
        if (!value) return false;
        const compact = value.trim();
        if (!compact) return false;
        if (compact.length < 3 || compact.length > 84) return false;
        if (compact.includes('|')) return false;
        if (/^(?:[-*+]\s+|\d+\.\s+)/.test(compact)) return false;
        if (/[.?!]$/.test(compact)) return false;
        return /:$/.test(compact);
    };

    const isSpecialLine = (line, nextLine = '') => {
        if (!line) return true;
        if (/^@@CODEBLOCK_\d+@@$/.test(line)) return true;
        if (/^(#{1,4})\s+/.test(line)) return true;
        if (/^(=){3,}$/.test(nextLine) || /^(-){3,}$/.test(nextLine)) return true;
        if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) return true;
        if (/^&gt;\s?/.test(line)) return true;
        if (/^[-*+]\s+/.test(line)) return true;
        if (/^[•●▪◦]\s+/.test(line)) return true;
        if (/^\d+\.\s+/.test(line)) return true;
        if (line.includes('|') && isTableSeparator(nextLine)) return true;
        return false;
    };

    while (i < lines.length) {
        const rawLine = lines[i];
        const line = rawLine.trim();

        if (!line) {
            flushList();
            i += 1;
            continue;
        }

        if (/^@@CODEBLOCK_\d+@@$/.test(line)) {
            flushList();
            resetOrderedSequence();
            html.push(line);
            i += 1;
            continue;
        }

        const headingMatch = line.match(/^(#{1,4})\s+(.+)$/);
        if (headingMatch) {
            flushList();
            resetOrderedSequence();
            const level = headingMatch[1].length;
            html.push(`<h${level}>${formatInline(headingMatch[2].trim())}</h${level}>`);
            i += 1;
            continue;
        }

        // Soporta titulos estilo "Setext" en markdown:
        // Titulo
        // ======
        if (i + 1 < lines.length) {
            const nextLine = lines[i + 1].trim();
            if (/^(=){3,}$/.test(nextLine)) {
                flushList();
                resetOrderedSequence();
                html.push(`<h1>${formatInline(line)}</h1>`);
                i += 2;
                continue;
            }
            if (/^(-){3,}$/.test(nextLine) && !line.includes('|')) {
                flushList();
                resetOrderedSequence();
                html.push(`<h2>${formatInline(line)}</h2>`);
                i += 2;
                continue;
            }
        }

        // Titulo heuristico para lineas cortas que cierran con ":".
        if (isLikelyTitleLine(line)) {
            flushList();
            resetOrderedSequence();
            const titleText = line.replace(/:\s*$/, '').trim();
            html.push(`<h3>${formatInline(titleText)}</h3>`);
            i += 1;
            continue;
        }

        const boldTitleMatch = line.match(/^(?:\*\*|__)([^*_].{1,84}?)(?:\*\*|__):?$/);
        if (boldTitleMatch) {
            flushList();
            resetOrderedSequence();
            html.push(`<h4>${formatInline(boldTitleMatch[1].trim())}</h4>`);
            i += 1;
            continue;
        }

        if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
            flushList();
            resetOrderedSequence();
            html.push('<hr>');
            i += 1;
            continue;
        }

        if (/^&gt;\s?/.test(line)) {
            flushList();
            resetOrderedSequence();
            const quoteLines = [];
            while (i < lines.length && /^&gt;\s?/.test(lines[i].trim())) {
                quoteLines.push(lines[i].trim().replace(/^&gt;\s?/, ''));
                i += 1;
            }
            html.push(`<blockquote>${quoteLines.map((q) => formatInline(q)).join('<br>')}</blockquote>`);
            continue;
        }

        if (line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1].trim())) {
            flushList();
            resetOrderedSequence();
            const headers = parseTableRow(line);
            i += 2; // saltar header + separador

            const rows = [];
            while (i < lines.length) {
                const rowLine = lines[i].trim();
                if (!rowLine || !rowLine.includes('|')) break;
                rows.push(parseTableRow(rowLine));
                i += 1;
            }

            // Fallback para "tabla rota": convertir pseudo-filas en tarjetas comparativas.
            if (rows.length <= 1) {
                const rowStartRe = /^\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|?\s*$/;
                const looseRows = [];
                let cursor = i;

                // Incluir la primera fila parcial si existe.
                if (rows.length === 1 && rows[0].length >= 2) {
                    looseRows.push({
                        title: rows[0][0],
                        price: rows[0][1],
                        bullets: [],
                    });
                }

                while (cursor < lines.length) {
                    const current = lines[cursor].trim();
                    if (!current) break;

                    const startMatch = current.match(rowStartRe);
                    if (startMatch) {
                        looseRows.push({
                            title: formatInline(cleanLooseTableArtifacts(startMatch[1].trim())),
                            price: formatInline(cleanLooseTableArtifacts(startMatch[2].trim())),
                            bullets: [],
                        });
                        cursor += 1;
                        continue;
                    }

                    const bulletMatch = current.match(/^[-*+]\s+(.+)$/) || current.match(/^[•●▪◦]\s+(.+)$/);
                    if (bulletMatch && looseRows.length > 0) {
                        looseRows[looseRows.length - 1].bullets.push(
                            formatInline(cleanLooseTableArtifacts(bulletMatch[1].trim()))
                        );
                        cursor += 1;
                        continue;
                    }

                    // Si viene linea no-bullet, la agregamos como detalle limpio.
                    const looseText = cleanLooseTableArtifacts(current);
                    if (looseText && looseRows.length > 0) {
                        looseRows[looseRows.length - 1].bullets.push(formatInline(looseText));
                        cursor += 1;
                        continue;
                    }

                    break;
                }

                if (looseRows.length >= 2) {
                    const priceLabel = headers[1] ? headers[1].replace(/<[^>]*>/g, '') : 'Precio';
                    html.push(renderComparisonCards(looseRows, priceLabel));
                    i = cursor;
                    continue;
                }
            }

            const thead = `<thead><tr>${headers.map((cell) => `<th>${cell}</th>`).join('')}</tr></thead>`;
            const tbody =
                rows.length > 0
                    ? `<tbody>${rows
                          .map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join('')}</tr>`)
                          .join('')}</tbody>`
                    : '';
            html.push(`<div class="table-wrap"><table>${thead}${tbody}</table></div>`);
            continue;
        }

        const unorderedMatch = line.match(/^[-*+]\s+(.+)$/);
        if (unorderedMatch) {
            if (listType !== 'ul') {
                flushList();
                listType = 'ul';
            }
            listItems.push(formatInline(unorderedMatch[1].trim()));
            i += 1;
            continue;
        }

        const dotBulletMatch = line.match(/^[•●▪◦]\s+(.+)$/);
        if (dotBulletMatch) {
            if (listType !== 'ul') {
                flushList();
                listType = 'ul';
            }
            listItems.push(formatInline(dotBulletMatch[1].trim()));
            i += 1;
            continue;
        }

        const orderedMatch = line.match(/^(\d+)\.\s+(.+)$/);
        if (orderedMatch) {
            if (listType !== 'ol') {
                flushList();
                listType = 'ol';
            }
            orderedItemNumbers.push(parseInt(orderedMatch[1], 10) || 1);
            listItems.push(formatInline(orderedMatch[2].trim()));
            i += 1;
            continue;
        }

        // Parrafo normal: agrupar lineas continuas hasta encontrar un bloque especial.
        flushList();
        const firstParagraphLine = cleanLooseTableArtifacts(line);
        const paragraphLines = firstParagraphLine ? [firstParagraphLine] : [];
        i += 1;

        while (i < lines.length) {
            const next = lines[i].trim();
            const nextNext = i + 1 < lines.length ? lines[i + 1].trim() : '';
            if (!next || isSpecialLine(next, nextNext)) break;
            const cleanedNext = cleanLooseTableArtifacts(next);
            if (cleanedNext) {
                paragraphLines.push(cleanedNext);
            }
            i += 1;
        }

        if (paragraphLines.length > 0) {
            html.push(`<p>${paragraphLines.map((pLine) => formatInline(pLine)).join('<br>')}</p>`);
        }
    }

    flushList();

    // Restaurar bloques de codigo tokenizados.
    let rendered = html.join('');
    rendered = rendered.replace(/@@CODEBLOCK_(\d+)@@/g, (match, idx) => codeBlocks[Number(idx)] || '');

    if (!rendered.trim()) {
        return formatInline(safeText).replace(/\n/g, '<br>');
    }

    return rendered;
}

// ==========================================
// PELICULAS — Fase 6.5 Web
// ==========================================

/**
 * Muestra una tarjeta de pelicula con poster y botones
 */
function appendMovieCard(movie) {
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message agent';

    const card = document.createElement('div');
    card.className = 'movie-card';

    const posterUrl = typeof movie.poster_url === 'string' ? movie.poster_url.trim() : '';
    const title = (movie.title || 'Desconocido').toString();
    const year = movie.year || '';
    const tmdbId = Number(movie.tmdbId || 0);
    const genreText = Array.isArray(movie.genres) && movie.genres.length
        ? movie.genres.filter(Boolean).join(', ')
        : (movie.genre_text || 'No especificado');
    const runtimeText = movie.runtime_text
        || (Number(movie.runtime_minutes) > 0
            ? `${Math.floor(Number(movie.runtime_minutes) / 60) > 0 ? `${Math.floor(Number(movie.runtime_minutes) / 60)}h ` : ''}${Number(movie.runtime_minutes) % 60}min`.trim()
            : 'No especificada');
    const rawSummary = (movie.summary || movie.overview || 'Sin resumen disponible.').toString();
    const summary = rawSummary.length > 170 ? `${rawSummary.slice(0, 170).trim()}...` : rawSummary;

    if (posterUrl) {
        const poster = document.createElement('img');
        poster.className = 'movie-poster';
        poster.src = posterUrl;
        poster.alt = `Poster de ${title}`;
        poster.loading = 'lazy';
        poster.referrerPolicy = 'no-referrer';
        poster.onerror = () => poster.remove();
        card.appendChild(poster);
    }

    const infoDiv = document.createElement('div');
    infoDiv.className = 'movie-info';

    const titleDiv = document.createElement('div');
    titleDiv.className = 'movie-title';
    titleDiv.textContent = year ? `${title} (${year})` : title;
    infoDiv.appendChild(titleDiv);

    const metaDiv = document.createElement('div');
    metaDiv.className = 'movie-meta';

    const genreRow = document.createElement('div');
    genreRow.className = 'movie-meta-row';
    genreRow.innerHTML = `<span class="movie-meta-label">Genero</span><span class="movie-meta-value">${escapeHtml(genreText)}</span>`;
    metaDiv.appendChild(genreRow);

    const runtimeRow = document.createElement('div');
    runtimeRow.className = 'movie-meta-row';
    runtimeRow.innerHTML = `<span class="movie-meta-label">Duracion</span><span class="movie-meta-value">${escapeHtml(runtimeText)}</span>`;
    metaDiv.appendChild(runtimeRow);

    infoDiv.appendChild(metaDiv);

    const summaryDiv = document.createElement('div');
    summaryDiv.className = 'movie-summary';
    summaryDiv.textContent = summary;
    infoDiv.appendChild(summaryDiv);

    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'movie-actions';

    const downloadBtn = document.createElement('button');
    downloadBtn.className = 'movie-btn movie-btn-download';
    downloadBtn.dataset.tmdb = String(tmdbId);
    downloadBtn.dataset.title = title;
    downloadBtn.dataset.year = String(year || 0);
    downloadBtn.textContent = 'Descargar';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'movie-btn movie-btn-cancel';
    cancelBtn.textContent = 'Cancelar';

    actionsDiv.appendChild(downloadBtn);
    actionsDiv.appendChild(cancelBtn);
    infoDiv.appendChild(actionsDiv);
    card.appendChild(infoDiv);

    // Event: Descargar
    downloadBtn.addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const actionsDiv = card.querySelector('.movie-actions');
        actionsDiv.innerHTML = '<div class="movie-loading">Buscando opciones de descarga...</div>';
        await fetchReleases(card, parseInt(btn.dataset.tmdb), btn.dataset.title, btn.dataset.year);
    });

    // Event: Cancelar
    cancelBtn.addEventListener('click', () => {
        card.querySelector('.movie-actions').innerHTML = '<div class="movie-status">Busqueda cancelada.</div>';
    });

    messageDiv.appendChild(card);
    chatMessages.appendChild(messageDiv);
    scrollToBottom();
}

/**
 * Busca releases disponibles y muestra opciones de calidad
 */
async function fetchReleases(card, tmdbId, title, year) {
    try {
        const resp = await fetch('/movie/add-and-releases', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tmdb_id: tmdbId, title, year: parseInt(year) || 0 }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `Error ${resp.status}`);
        }

        const data = await resp.json();
        const releases = data.releases || [];

        const actionsDiv = card.querySelector('.movie-actions') || card.querySelector('.movie-info');

        if (releases.length === 0) {
            actionsDiv.innerHTML = `
                <div class="movie-status">
                    No se encontraron releases disponibles.
                    <br>La pelicula queda monitoreada en Radarr.
                </div>
            `;
            return;
        }

        renderReleaseOptions(card, releases, title, year, tmdbId);

    } catch (err) {
        console.error('Error buscando releases:', err);
        const actionsDiv = card.querySelector('.movie-actions') || card.querySelector('.movie-info');
        actionsDiv.innerHTML = `<div class="movie-status movie-error">Error: ${err.message}</div>`;
    }
}

/**
 * Renderiza las opciones de calidad como botones minimalistas
 */
function renderReleaseOptions(card, releases, title, year, tmdbId) {
    const actionsDiv = card.querySelector('.movie-actions') || card.querySelector('.movie-info');

    let html = '<div class="release-options">';

    releases.slice(0, 6).forEach((rel, idx) => {
        const cat = rel.quality_category || rel.quality || '?';
        const size = rel.size_formatted || '?';
        const seeders = rel.seeders || 0;
        const langs = (rel.languages || []).join(', ') || '?';
        const protocol = (rel.protocol || '').toUpperCase();
        const indexer = rel.indexer || '?';
        const key = `rel_${idx}_${Date.now()}`;

        // Guardar en cache
        pendingReleases[key] = {
            guid: rel.guid,
            indexerId: rel.indexerId,
            tmdbId: tmdbId,
            title, year, quality: cat, size,
        };

        html += `
            <button class="release-btn" data-key="${key}">
                <span class="release-quality">${cat}</span>
                <span class="release-size">${size}</span>
                <span class="release-meta">${seeders} seeds · ${protocol} · ${langs}</span>
            </button>
        `;
    });

    html += '<button class="release-btn release-btn-cancel">Cancelar</button>';
    html += '</div>';

    actionsDiv.innerHTML = html;

    // Events para cada release
    actionsDiv.querySelectorAll('.release-btn[data-key]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const key = btn.dataset.key;
            const info = pendingReleases[key];
            if (!info) return;

            actionsDiv.innerHTML = `<div class="movie-loading">Descargando ${info.quality} (${info.size})...</div>`;
            await grabRelease(card, info);
        });
    });

    // Event cancelar
    const cancelBtn = actionsDiv.querySelector('.release-btn-cancel');
    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => {
            actionsDiv.innerHTML = '<div class="movie-status">Descarga cancelada.</div>';
        });
    }

    scrollToBottom();
}

/**
 * Graba (descarga) un release especifico
 */
async function grabRelease(card, info) {
    const actionsDiv = card.querySelector('.movie-actions') || card.querySelector('.movie-info');

    try {
        const resp = await fetch('/movie/grab', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ guid: info.guid, indexer_id: info.indexerId, tmdb_id: info.tmdbId || null }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `Error ${resp.status}`);
        }

        actionsDiv.innerHTML = `
            <div class="movie-status movie-success">
                Descarga iniciada!<br>
                ${info.title} (${info.year}) · ${info.quality} (${info.size})<br>
                Transmission ya esta descargandola.
            </div>
        `;
    } catch (err) {
        console.error('Error grabando release:', err);
        actionsDiv.innerHTML = `<div class="movie-status movie-error">Error: ${err.message}</div>`;
    }
}

// ==========================================
// AUTO-RESIZE DEL TEXTAREA
// ==========================================

function autoResizeTextarea() {
    messageInput.style.height = 'auto';
    const newHeight = Math.min(messageInput.scrollHeight, 120);
    messageInput.style.height = newHeight + 'px';
}

function updateSendButton() {
    const hasText = messageInput.value.trim().length > 0;
    sendBtn.classList.toggle('active', hasText);
}

// ==========================================
// VERIFICACION DE SALUD DEL SERVIDOR
// ==========================================

async function checkHealth() {
    try {
        const response = await fetch(HEALTH_URL);
        const data = await response.json();

        if (data.status === 'ok' && data.ollama) {
            statusDot.className = 'status-dot online';
            if (statusText) statusText.textContent = 'En línea';
        } else if (data.status === 'ok') {
            statusDot.className = 'status-dot offline';
            if (statusText) statusText.textContent = 'Ollama desconectado';
        } else {
            statusDot.className = 'status-dot offline';
            if (statusText) statusText.textContent = 'Error';
        }
    } catch {
        statusDot.className = 'status-dot offline';
        if (statusText) statusText.textContent = 'Sin conexión';
    }
}

// ==========================================
// MODO OSCURO
// ==========================================

function initTheme() {
    const saved = localStorage.getItem('theme');
    const normalized = saved === 'light' || saved === 'dark' ? saved : 'dark';
    document.documentElement.setAttribute('data-theme', normalized);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
}

// ==========================================
// LANDING → CHAT TRANSITION
// ==========================================

/**
 * Transiciona del landing al modo chat con animación
 */
function transitionToChat(initialText) {
    if (!isLandingMode) return;
    isLandingMode = false;
    document.body.classList.add('chat-started');

    // Fade out landing
    landingScreen.classList.add('fade-out');

    setTimeout(() => {
        landingScreen.classList.add('hidden');

        // Show chat and input
        chatContainer.classList.remove('hidden');
        inputArea.classList.remove('hidden');
        inputArea.classList.add('slide-up');

        // Focus the chat input
        messageInput.focus();

        // Send the first message if there's text
        if (initialText) {
            messageInput.value = initialText;
            updateSendButton();
            sendMessage();
        }
    }, 450);
}

/**
 * Envía mensaje desde el landing
 */
function sendFromLanding() {
    const text = messageInputLanding.value.trim();
    if (!text) return;
    transitionToChat(text);
}

// ==========================================
// TOOLS MENU
// ==========================================

function toggleToolsMenu(btn, menu) {
    const isVisible = menu.classList.contains('visible');
    // Cerrar todos los menus primero
    document.querySelectorAll('.tools-menu').forEach(m => m.classList.remove('visible'));
    document.querySelectorAll('.tools-btn').forEach(b => b.classList.remove('active'));
    closeAllModelMenus();
    closeAllSourceMenus();

    if (!isVisible) {
        menu.classList.add('visible');
        btn.classList.add('active');
    }
}

function closeAllToolsMenus() {
    document.querySelectorAll('.tools-menu').forEach(m => m.classList.remove('visible'));
    document.querySelectorAll('.tools-btn').forEach(b => b.classList.remove('active'));
}

// ==========================================
// NEWS PANEL
// ==========================================

async function toggleNewsPanel() {
    closeAllToolsMenus();

    if (newsPanel.classList.contains('hidden')) {
        newsPanel.classList.remove('hidden');
        if (!newsLoaded) {
            await loadNews();
        }
    } else {
        newsPanel.classList.add('hidden');
    }
}

async function loadNews() {
    newsPanelContent.innerHTML = '<p class="news-placeholder">Cargando noticias...</p>';
    try {
        // Intentar cargar noticias del backend
        const resp = await fetch('/news');
        if (resp.ok) {
            const data = await resp.json();
            renderNews(data.articles || data.news || []);
            newsLoaded = true;
            return;
        }
    } catch (e) {
        // fallback silencioso
    }

    // Noticias placeholder si no hay backend
    newsPanelContent.innerHTML = `
        <div class="news-item">
            <div class="news-item-source">Tecnología</div>
            <div class="news-item-title">Las noticias se cargarán cuando configures una fuente</div>
            <div class="news-item-desc">Puedes conectar una API de noticias para ver contenido aquí en tiempo real.</div>
        </div>
    `;
    newsLoaded = true;
}

function renderNews(articles) {
    if (!articles.length) {
        newsPanelContent.innerHTML = '<p class="news-placeholder">No hay noticias disponibles.</p>';
        return;
    }
    newsPanelContent.innerHTML = articles.slice(0, 10).map(a => `
        <div class="news-item">
            <div class="news-item-source">${escapeHtml(a.source || 'Noticias')}</div>
            <div class="news-item-title">${escapeHtml(a.title || '')}</div>
            ${a.description ? `<div class="news-item-desc">${escapeHtml(a.description)}</div>` : ''}
        </div>
    `).join('');
}

// ==========================================
// EVENT LISTENERS
// ==========================================

// --- Landing events ---
sendBtnLanding.addEventListener('click', sendFromLanding);
messageInputLanding.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendFromLanding();
    }
});
messageInputLanding.addEventListener('input', () => {
    messageInputLanding.style.height = 'auto';
    messageInputLanding.style.height = Math.min(messageInputLanding.scrollHeight, 120) + 'px';
    const hasText = messageInputLanding.value.trim().length > 0;
    sendBtnLanding.classList.toggle('active', hasText);
    if (messageInputLanding.value.length > 0) {
        closeAllModelMenus();
    }
});

// --- Chat events ---
sendBtn.addEventListener('click', sendMessage);
messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});
messageInput.addEventListener('input', () => {
    autoResizeTextarea();
    updateSendButton();
    if (messageInput.value.length > 0) {
        closeAllModelMenus();
    }
});

// Toggle tema
themeToggle.addEventListener('click', toggleTheme);

// --- Tools menu events ---
toolsBtnLanding.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleToolsMenu(toolsBtnLanding, toolsMenuLanding);
});
toolsBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleToolsMenu(toolsBtn, toolsMenu);
});

// --- Model picker events ---
if (modelPickerBtnLanding && modelPickerMenuLanding) {
    modelPickerBtnLanding.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleModelPicker(modelPickerBtnLanding, modelPickerMenuLanding);
    });
    modelPickerMenuLanding.addEventListener('click', (e) => e.stopPropagation());
}
if (modelPickerBtn && modelPickerMenu) {
    modelPickerBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleModelPicker(modelPickerBtn, modelPickerMenu);
    });
    modelPickerMenu.addEventListener('click', (e) => e.stopPropagation());
}

// News toggle
toggleNewsLanding.addEventListener('click', toggleNewsPanel);
toggleNews.addEventListener('click', toggleNewsPanel);
newsPanelClose.addEventListener('click', () => newsPanel.classList.add('hidden'));

// Cerrar menús al hacer click fuera
document.addEventListener('click', (e) => {
    if (!e.target.closest('.tools-btn') && !e.target.closest('.tools-menu')) {
        closeAllToolsMenus();
    }
    if (!e.target.closest('.model-picker')) {
        closeAllModelMenus();
    }
    if (!e.target.closest('.source-action')) {
        closeAllSourceMenus();
    }
});

// ==========================================
// INICIALIZACION
// ==========================================

// Tema
initTheme();
loadModelPreferences();
ensureModelSelectionIsValid();
updateModelPickerButtons();
renderModelPickerMenus();
loadLLMCatalog();

// Saludo aleatorio en el landing
landingGreeting.textContent = getRandomGreeting();

// Verificar salud al cargar y cada 30 segundos
checkHealth();
setInterval(checkHealth, 30000);

// Focus en input del landing
messageInputLanding.focus();

console.log('RUFÜS UI cargada correctamente');
