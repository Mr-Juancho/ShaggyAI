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
const statusText = document.getElementById('statusText');

// --- Configuracion ---
const API_URL = '/chat';
const HEALTH_URL = '/health';
const USER_ID = 'desktop_user';
const SOURCE = 'desktop';

// --- Estado ---
let isWaiting = false;

// ==========================================
// FUNCIONES PRINCIPALES
// ==========================================

/**
 * Envia un mensaje al backend y muestra la respuesta
 */
async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text || isWaiting) return;

    // Mostrar mensaje del usuario
    appendMessage(text, 'user');

    // Limpiar input
    messageInput.value = '';
    autoResizeTextarea();
    updateSendButton();

    // Mostrar indicador de escribiendo
    const typingEl = showTypingIndicator();

    // Bloquear envio
    isWaiting = true;
    sendBtn.disabled = true;

    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 150000); // 2.5 min timeout

        const response = await fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: text,
                user_id: USER_ID,
                source: SOURCE
            }),
            signal: controller.signal
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
            throw new Error(`Error HTTP: ${response.status}`);
        }

        const data = await response.json();

        // Quitar indicador de escribiendo
        removeTypingIndicator(typingEl);

        // Mostrar respuesta del agente
        appendMessage(data.response, 'agent');

    } catch (error) {
        console.error('Error al enviar mensaje:', error);
        removeTypingIndicator(typingEl);
        const errorMsg = error.name === 'AbortError'
            ? 'La respuesta tardo demasiado. El modelo puede estar sobrecargado, intenta de nuevo.'
            : 'No pude conectarme al servidor. Verifica que este corriendo en localhost:8000.';
        appendMessage(errorMsg, 'agent');
    } finally {
        isWaiting = false;
        sendBtn.disabled = false;
        messageInput.focus();
    }
}

/**
 * Agrega un mensaje al area de chat
 */
function appendMessage(text, role) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;

    const bubbleDiv = document.createElement('div');
    bubbleDiv.className = 'bubble';

    if (role === 'agent') {
        bubbleDiv.classList.add('markdown');
        bubbleDiv.innerHTML = formatText(text);
    } else {
        // Mostrar texto del usuario literal para conservar su formato.
        bubbleDiv.textContent = text;
    }

    messageDiv.appendChild(bubbleDiv);
    chatMessages.appendChild(messageDiv);

    // Auto-scroll suave
    scrollToBottom();
}

/**
 * Muestra el indicador de "escribiendo..."
 */
function showTypingIndicator() {
    const typingDiv = document.createElement('div');
    typingDiv.className = 'typing-indicator';
    typingDiv.innerHTML = `
        <div class="bubble">
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
        </div>
    `;
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

    // Negritas (**texto** o __texto__)
    formatted = formatted.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    formatted = formatted.replace(/__([^_]+)__/g, '<strong>$1</strong>');

    // Cursiva (*texto* o _texto_)
    formatted = formatted.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
    formatted = formatted.replace(/_([^_\n]+)_/g, '<em>$1</em>');

    // Tachado (~~texto~~)
    formatted = formatted.replace(/~~([^~]+)~~/g, '<del>$1</del>');

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

    const hasAllowedTag = /<\/?(table|thead|tbody|tr|th|td|br|ul|ol|li|p|strong|em|b|i)\b/i.test(rawText);
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
    ].forEach(allowSimpleTag);

    html = html.replace(/&lt;\s*br\s*\/?\s*&gt;/gi, '<br>');

    // Si no queda ninguna etiqueta permitida real, no usar este modo.
    if (!/<(table|thead|tbody|tr|th|td|br|ul|ol|li|p|strong|em|b|i)\b/i.test(html)) {
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

    const flushList = () => {
        if (!listType || listItems.length === 0) return;
        const tag = listType === 'ol' ? 'ol' : 'ul';
        html.push(`<${tag} class="md-list">${listItems.map((item) => `<li>${item}</li>`).join('')}</${tag}>`);
        listType = null;
        listItems = [];
    };

    const isSpecialLine = (line, nextLine = '') => {
        if (!line) return true;
        if (/^@@CODEBLOCK_\d+@@$/.test(line)) return true;
        if (/^(#{1,4})\s+/.test(line)) return true;
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
            html.push(line);
            i += 1;
            continue;
        }

        const headingMatch = line.match(/^(#{1,4})\s+(.+)$/);
        if (headingMatch) {
            flushList();
            const level = headingMatch[1].length;
            html.push(`<h${level}>${formatInline(headingMatch[2].trim())}</h${level}>`);
            i += 1;
            continue;
        }

        if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
            flushList();
            html.push('<hr>');
            i += 1;
            continue;
        }

        if (/^&gt;\s?/.test(line)) {
            flushList();
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

        const orderedMatch = line.match(/^\d+\.\s+(.+)$/);
        if (orderedMatch) {
            if (listType !== 'ol') {
                flushList();
                listType = 'ol';
            }
            listItems.push(formatInline(orderedMatch[1].trim()));
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
            statusText.textContent = 'En línea';
        } else if (data.status === 'ok') {
            statusDot.className = 'status-dot offline';
            statusText.textContent = 'Ollama desconectado';
        } else {
            statusDot.className = 'status-dot offline';
            statusText.textContent = 'Error';
        }
    } catch {
        statusDot.className = 'status-dot offline';
        statusText.textContent = 'Sin conexión';
    }
}

// ==========================================
// MODO OSCURO
// ==========================================

function initTheme() {
    const saved = localStorage.getItem('theme');
    if (saved) {
        document.documentElement.setAttribute('data-theme', saved);
    } else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
        document.documentElement.setAttribute('data-theme', 'dark');
    }
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
}

// ==========================================
// EVENT LISTENERS
// ==========================================

// Enviar con boton
sendBtn.addEventListener('click', sendMessage);

// Enviar con Enter (Shift+Enter para nueva linea)
messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// Auto-resize y actualizar boton mientras se escribe
messageInput.addEventListener('input', () => {
    autoResizeTextarea();
    updateSendButton();
});

// Toggle tema
themeToggle.addEventListener('click', toggleTheme);

// ==========================================
// INICIALIZACION
// ==========================================

// Tema
initTheme();

// Verificar salud al cargar y cada 30 segundos
checkHealth();
setInterval(checkHealth, 30000);

// Focus en input
messageInput.focus();

console.log('Shaggy UI cargada correctamente');
